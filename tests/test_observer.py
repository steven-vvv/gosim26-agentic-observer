"""agents/observer 的单元测试：与官方 kit 口径一致性、覆盖边际方向、协议往返。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agents" / "observer"))
sys.path.insert(0, str(ROOT / "kit" / "agent"))

import scoring_preview  # 官方基线的公开估值
from policy import DEFAULTS, ObserverPolicy, epoch, jain

SCORE_CONFIG = {
    "schema_version": "challenge-score-v3",
    "quality_thresholds": {"dark": 0.65, "bright": 0.4},
    "program_bonus": {"DARK": 0.25, "BRIGHT": 0.15, "BACKUP": 0.08},
    "penalties": {"unsafe_observation": 2000.0, "invalid_action": 100.0, "avoidable_wait_per_second": 0.001,
                  "required_miss": 1000.0, "flexible_shortfall_per_tile": 100.0},
    "flexible_quota_per_region": 4,
    "coverage_bonus_weight": 0.35,
}
CONTRACT = {"score_config": SCORE_CONFIG,
            "weather_score_interface": {"airmass_exponent": 1.0, "maximum_weather_quality": 3.0},
            "lunar_model": {"angular_decay_scale_deg": 35.0, "altitude_exponent": 1.0, "maximum_penalty": 0.75}}
SITE = {"latitude_deg": 31.9634, "longitude_deg": -111.599, "utc_offset_hours": -7.0, "sun_altitude_limit_deg": -12.0}


def initial(n_regions: int = 2) -> dict:
    tiles = [{"tile_id": f"T{i:05d}", "region_id": f"R{i % n_regions:02d}", "scheduling_class": "FLEXIBLE",
              "nominal_exptime_seconds": 900, "available_from_utc": "2026-10-06T02:00:00Z",
              "available_until_utc": "2026-11-04T12:45:00Z", "tile_science_value": 200.0,
              "ra_deg": str(20.0 * i), "dec_deg": "30.0"}
             for i in range(1, 7)]
    return {"schema_version": "initial-publication-v2", "scoring_contract": CONTRACT, "site": SITE,
            "calendar": {"first_night": "2026-10-05", "last_night": "2026-10-07", "night_count": 3,
                         "slot_duration_seconds": 900},
            "tile_catalog": {"region_ids": [f"R{r:02d}" for r in range(n_regions)], "required_tile_ids": [], "tiles": tiles}}


def candidate(tile: str, region: str, airmass: float, seeing: float = 1.0, value: float = 200.0) -> dict:
    weather = {"is_observable": True, "seeing_arcsec": seeing, "transparency": 0.9, "sky_quality": 1.0, "active_event_ids": []}
    return {"tile_id": tile, "region_id": region, "scheduling_class": "FLEXIBLE", "nominal_exptime_seconds": 900,
            "tile_science_value": value, "window_start_utc": "2026-10-06T02:00:00Z", "window_end_utc": "2026-10-06T12:00:00Z",
            "geometry": {"airmass": airmass, "lunar_quality_factor": 0.97}, "effective_weather": weather,
            "already_completed": False}


def snapshot(candidates: list, completed: list | None = None) -> dict:
    return {"schema_version": "decision-snapshot-v3", "decision_sequence": 1,
            "cursor": {"slot_id": "N20261005-S001", "night_id": "N20261005", "timestamp_utc": "2026-10-06T02:00:00Z",
                       "slot_offset_seconds": 0},
            "current_site_weather": {"is_observable": True, "duration_seconds": 900, "active_event_ids": []},
            "candidate_tiles": candidates, "active_requests": [], "tile_last_finished": None,
            "progress": {"completed_tile_ids": completed or [], "flexible_completed_by_region": {"R00": 4, "R01": 4}}}


def test_jain_extremes():
    assert jain([5, 5, 5, 5]) == pytest.approx(1.0)
    assert jain([8, 0, 0, 0]) == pytest.approx(0.25)
    assert jain([0, 0]) == 0.0


@pytest.mark.parametrize("airmass,seeing", [(1.0, 0.8), (1.3, 1.1), (1.9, 1.6), (1.05, 0.4)])
def test_quality_and_band_match_official_preview(airmass, seeing):
    policy = ObserverPolicy(initial(), dict(DEFAULTS))
    c = candidate("T00001", "R01", airmass, seeing)
    _, _, official = scoring_preview._combined_quality(c, CONTRACT["weather_score_interface"])
    ours = policy._quality(c["effective_weather"], c["geometry"])
    assert ours == pytest.approx(official)
    assert policy._band(ours) == scoring_preview._quality_band(official, SCORE_CONFIG)


def test_coverage_prefers_the_starved_region():
    """两块条件完全相同的天区，覆盖项应让策略选择完成数更少的分区。"""
    policy = ObserverPolicy(initial(), dict(DEFAULTS))
    policy.base_total = 1000.0
    snap = snapshot([candidate("T00002", "R00", 1.2), candidate("T00001", "R01", 1.2)],
                    completed=["T00004", "T00006", "T00003"])  # R01 已有 T00003，R00 已有 T00004、T00006
    decision = policy.decide(snap)
    assert decision["tile_id"] == "T00001"


def test_model_choices_drive_planning_interval_and_action():
    class Advisor:
        enabled = True
        calls = 0
        failures = 0

        def choose(self, stage, options, context):
            self.calls += 1
            assert len(options) == 2
            assert context["night_id"] == "N20261005"
            return {"plan_adaptation": "economy", "action_decision": "C1"}[stage]

    initial_data = initial()
    initial_data["tile_catalog"]["tiles"][1]["ra_deg"] = initial_data["tile_catalog"]["tiles"][0]["ra_deg"]
    snap = snapshot([candidate("T00001", "R01", 1.2), candidate("T00002", "R00", 1.2)])
    params = dict(DEFAULTS, plan_beta=0)
    baseline = ObserverPolicy(initial_data, params)
    expected = baseline.decide(snap)
    policy = ObserverPolicy(initial_data, params)
    policy.advisor = Advisor()
    decision = policy.decide(snap)
    assert policy.advised_every == 3600
    assert policy.advisor.calls == 2
    assert decision["action"] == "observe"
    assert decision["tile_id"] != expected["tile_id"]
    assert decision["decision_source"] == "model"


def test_model_advisor_validates_choices_and_stops_after_failures(monkeypatch):
    from model_advisor import ModelAdvisor

    advisor = ModelAdvisor("https://model.example/v1", "secret", timeout=0.1)
    options = [{"id": "C0"}, {"id": "C1"}]

    class Response:
        def __init__(self, choice):
            self.choice = choice

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return json.dumps({"choices": [{"message": {"content": json.dumps({"choice": self.choice})}}]}).encode()

    def answer(request, timeout):
        assert request.full_url == "https://model.example/v1/chat/completions"
        assert timeout == pytest.approx(0.2)
        assert json.loads(request.data)["model"] == "team-model"
        return Response("C1")

    monkeypatch.setattr("model_advisor.urllib.request.urlopen", answer)
    assert advisor.choose("action_decision", options, {}) == "C1"
    monkeypatch.setattr("model_advisor.urllib.request.urlopen", lambda *_args, **_kwargs: Response("invalid"))
    for _ in range(3):
        assert advisor.choose("action_decision", options, {}) is None
    assert not advisor.enabled
    assert advisor.calls == 4
    assert advisor.failures == 3


def test_model_advisor_bounds_successful_stage_calls(monkeypatch):
    from model_advisor import ModelAdvisor

    advisor = ModelAdvisor("https://model.example/v1", "secret")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return b'{"choices":[{"message":{"content":"{\\"choice\\":\\"C0\\"}"}}]}'

    monkeypatch.setattr("model_advisor.urllib.request.urlopen", lambda *_args, **_kwargs: Response())
    options = [{"id": "C0"}, {"id": "C1"}]
    for _ in range(5):
        assert advisor.choose("plan_adaptation", options, {}) in {"C0", None}
    assert advisor.calls == 4
    assert advisor.stage_calls == {"plan_adaptation": 4}


def test_model_advisor_guided_mode_keeps_deterministic_choice_below_threshold(monkeypatch):
    from model_advisor import AdvisorConfig, ModelAdvisor

    advisor = ModelAdvisor(
        "https://model.example/v1",
        "secret",
        config=AdvisorConfig(min_alternative_probability=0.85),
    )

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return (b'{"choices":[{"message":{"content":"'
                    b'{\\"choice\\":\\"C1\\",\\"probabilities\\":'
                    b'{\\"C0\\":0.6,\\"C1\\":0.4}}"}}]}')

    monkeypatch.setattr("model_advisor.urllib.request.urlopen", lambda *_args, **_kwargs: Response())
    assert advisor.choose("action_decision", [{"id": "C0"}, {"id": "C1"}],
                          {"deterministic_choice": "C0"}) is None
    assert advisor.last_outcome["guided_override"] is True
    assert advisor.last_outcome["accepted_choice"] is None


def test_model_advisor_request_keeps_live_state_at_dynamic_suffix(monkeypatch):
    from model_advisor import ModelAdvisor

    advisor = ModelAdvisor("https://model.example/v1", "secret")
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return b'{"choices":[{"message":{"content":"{\\"choice\\":\\"C0\\"}"}}]}'

    def answer(request, timeout):
        captured["body"] = json.loads(request.data)
        return Response()

    monkeypatch.setattr("model_advisor.urllib.request.urlopen", answer)
    context = {
        "schema": "gosim26.observer.live_state.v1",
        "night_id": "N20261005",
        "weekly": {"weather_forecast": [{"condition": "cloudy"}]},
    }
    assert advisor.choose("action_decision", [{"id": "C0"}, {"id": "C1"}], context) == "C0"
    assert [message["role"] for message in captured["body"]["messages"]] == ["system", "user"]
    dynamic = json.loads(captured["body"]["messages"][-1]["content"])
    assert dynamic["live_state"]["weekly"]["weather_forecast"] == [{"condition": "cloudy"}]


def test_agent_process_round_trip():
    """整个 agent 进程：一条 initialize + 一条 decision_request，输出一条合法的 decision_response。"""
    envelope = {"protocol_version": "participant-agent-protocol-v2"}
    lines = [dict(envelope, message_type="initialize", payload=initial()),
             dict(envelope, message_type="decision_request", decision_sequence=1,
                  payload=snapshot([candidate("T00001", "R01", 1.2)]))]
    proc = subprocess.run([sys.executable, str(ROOT / "agents" / "observer" / "main.py")],
                          input="".join(json.dumps(line) + "\n" for line in lines),
                          capture_output=True, text=True, timeout=60, check=True)
    response = json.loads(proc.stdout.strip().splitlines()[-1])
    assert response["message_type"] == "decision_response"
    assert response["decision_sequence"] == 1
    assert response["action"] == "observe" and response["tile_id"] == "T00001"
    assert "Traceback" not in proc.stderr
    assert 'observer: telemetry ' in proc.stderr and '"event":"runtime"' in proc.stderr
    assert '"event":"telemetry_config"' in proc.stderr
    assert '"event":"decision_trace"' in proc.stderr and '"policy_ms"' in proc.stderr
    assert '"event":"final"' in proc.stderr
    assert '"event":"telemetry_final"' in proc.stderr


@pytest.mark.parametrize("agent", ["observer", "public_once"])
def test_final_report_is_ready_before_the_last_response(agent, tmp_path):
    publication = initial()
    final_snapshot = snapshot([])
    final_snapshot["cursor"] = {
        "slot_id": "N20261007-S001", "night_id": "N20261007",
        "timestamp_utc": "2026-10-08T10:45:00Z", "slot_offset_seconds": 0,
    }
    final_snapshot["night_start"] = {"night": {"observing_end_utc": "2026-10-08T11:00:00Z"}}
    entry = ROOT / "agents" / "observer" / "main.py"
    if agent == "public_once":
        from tools.pack_project import build

        project = tmp_path / "project"
        build(ROOT / "agents" / "public_once", project, tmp_path / "package.zip", None,
              "python:3.12-slim", public_schedules=ROOT / "agents" / "public_once" / "public_schedules.json",
              telemetry_source=ROOT / "agents" / "observer" / "telemetry.py")
        entry = project / "main.py"
    envelope = {"protocol_version": "participant-agent-protocol-v2"}
    lines = [dict(envelope, message_type="initialize", payload=publication),
             dict(envelope, message_type="decision_request", decision_sequence=1, payload=final_snapshot)]
    proc = subprocess.run([sys.executable, str(entry)],
                          input="".join(json.dumps(line) + "\n" for line in lines),
                          capture_output=True, text=True, timeout=60, check=True,
                          env={**os.environ, "TELEMETRY_DISABLE": "1"})
    response = json.loads(proc.stdout.strip())
    assert response["message_type"] == "decision_response"
    records = [json.loads(line.split("observer: telemetry ", 1)[1]) for line in proc.stderr.splitlines()
               if line.startswith("observer: telemetry ")]
    final = next(record for record in records if record["event"] == "final")
    assert final["final_response_pending"] is True
    assert final["summary"]["actions"] == {"wait": 1}
    assert next(record for record in records if record["event"] == "final_delivery")["remote_delivered"] is False


def test_geometry_matches_official_simulator():
    """任意时刻的高度、airmass、月光因子与官方 tile_geometry_simulator 一致。"""
    import json as _json
    from datetime import datetime, timezone

    sys.path.insert(0, str(ROOT / "kit"))
    from challenge import tile_geometry_simulator as official
    from geometry import Site, TileGeometry

    scenario = ROOT / "data" / "scenarios" / "eval-a" / "config"
    calendar = _json.loads((scenario / "calendar_config.json").read_text())
    tile_config = _json.loads((scenario / "tile_config.json").read_text())
    site = Site(calendar["site"], tile_config["lunar_model"])
    for ra, dec, stamp in ((25.86, 6.10, "2026-10-06T05:00:00"), (310.0, 55.0, "2026-10-20T09:37:30"),
                           (120.0, -4.0, "2026-11-12T11:15:00"), (200.0, 40.0, "2026-10-26T06:00:00")):
        moment = datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc)
        tile = official.Tile("T", ra, dec, 900, "R00", "FLEXIBLE", moment, moment, 1, 1, 1, 1)
        expected = official.geometry_sample(tile, moment, tile_config, calendar)
        altitude, am, lunar = site.moment(moment.timestamp()).quality(TileGeometry(ra, dec))
        assert altitude == pytest.approx(expected["altitude_deg"], abs=1e-9)
        if altitude > 0:
            assert am == pytest.approx(expected["airmass"], rel=1e-9)
        assert lunar == pytest.approx(expected["lunar_quality_factor"], abs=1e-9)


def test_planner_numpy_geometry_matches_scalar():
    from planner import Planner, np

    if np is None:
        pytest.skip("numpy not installed")
    publication = initial()
    publication["tile_catalog"]["tiles"][0]["available_from_utc"] = "2026-10-06T07:00:00Z"
    publication["tile_catalog"]["tiles"][1]["available_until_utc"] = "2026-10-07T07:00:00Z"
    vectorized = Planner(publication, dict(DEFAULTS, plan_numpy_geometry=True, plan_trim_current_bin=True))
    scalar = Planner(publication, dict(DEFAULTS, plan_numpy_geometry=False, plan_trim_current_bin=True))
    for night in range(3):
        vectorized._compute_night(night)
        scalar._compute_night(night)
    for tid in scalar.tiles:
        assert vectorized.qmax[tid] == pytest.approx(scalar.qmax[tid], rel=1e-12)
        assert vectorized.qtime[tid] == scalar.qtime[tid]
        assert vectorized.qsamples[tid].keys() == scalar.qsamples[tid].keys()
        for b, samples in scalar.qsamples[tid].items():
            assert vectorized.qsamples[tid][b] == pytest.approx(samples, rel=1e-12)


def test_night_bounds_match_official_calendar():
    """重建的每夜观测时段与场景的官方 night_calendar.csv 完全一致。"""
    import csv as _csv
    import json as _json
    from datetime import date, datetime

    from geometry import Site

    scenario = ROOT / "scenarios" / "comp-a-s101"
    if not scenario.exists():
        pytest.skip("本地场景未生成")
    calendar = _json.loads((scenario / "config" / "calendar_config.json").read_text())
    site = Site(calendar["site"], CONTRACT["lunar_model"])
    with (scenario / "outputs" / "reference" / "night_calendar.csv").open(encoding="utf-8-sig") as handle:
        rows = list(_csv.DictReader(handle))
    for row in rows:
        start, end = site.night_bounds(date.fromisoformat(row["night_date"]), 900)
        assert start == datetime.fromisoformat(row["observing_start_utc"].replace("Z", "+00:00")).timestamp()
        assert end == datetime.fromisoformat(row["observing_end_utc"].replace("Z", "+00:00")).timestamp()


def test_global_dip_separates_local_anomaly_from_global_dip():
    """只有本分区偏低是局部异常；其他分区也普遍偏低（如漏报的寒潮）是全局下沉，不算故障/红化证据。"""
    policy = ObserverPolicy(initial(n_regions=4), dict(DEFAULTS))
    for region in ("R00", "R01", "R02", "R03") * 2:
        assert not policy._global_dip(region, 0.95)
    assert not policy._global_dip("R01", 0.55)          # 其他分区都正常：局部异常
    dip = ObserverPolicy(initial(n_regions=4), dict(DEFAULTS))
    for region in ("R00", "R02", "R03", "R00", "R02", "R03"):
        dip._global_dip(region, 0.66)                   # 其他分区普遍偏低
    assert dip._global_dip("R01", 0.66)


def test_posterior_expectation_uses_each_slots_weather():
    """事后期望：第二段用第二个时隙（这次快照）里该天区的有效天气；拿不到某段天气或站点有生效事件（寒潮）时为 None。"""
    policy = ObserverPolicy(initial(), dict(DEFAULTS))
    first = {"is_observable": True, "seeing_arcsec": 1.0, "transparency": 0.9, "sky_quality": 1.0, "active_event_ids": []}
    second = dict(first, seeing_arcsec=1.6)
    t0 = epoch("2026-10-06T02:00:00Z")
    pending = {"tile_id": "T00001", "program": "DARK", "value": 200.0, "weather": first, "site_events": False,
               "geometry": [(0.5, 1.2, 0.97), (0.5, 1.25, 0.97)], "slot_start": t0, "slot_seconds": 900.0}
    snap = snapshot([candidate("T00001", "R01", 1.25)])
    snap["candidate_tiles"][0]["effective_weather"] = second
    snap["cursor"] = {"timestamp_utc": "2026-10-06T02:22:30Z", "slot_offset_seconds": 450}
    now = t0 + 1350.0
    q1, q2 = 0.9 / 1.2 * 0.97, 0.9 / 1.6 / 1.25 * 0.97
    expected = 200.0 * sum(0.5 * q * (1.25 if q >= 0.65 else 1.0) for q in (q1, q2))
    assert policy._posterior_expected(pending, snap, now) == pytest.approx(expected)
    assert policy._posterior_expected(dict(pending, geometry=[*pending["geometry"], (0.1, 1.3, 0.97)]), snap, now) is None
    assert policy._posterior_expected(dict(pending, site_events=True), snap, now) is None
    assert policy._posterior_expected(pending, snap, now + 900.0) is None  # 已经进入第三个时隙：第二段的天气没见过


def test_exact_reads_report_tags_on_a_single_read():
    """精确比值：红化与 NOVA 都单读即报；普通比值的红化仍要同一天区两次读数。"""
    policy = ObserverPolicy(initial(), dict(DEFAULTS, exact_reads=True))
    read = {"tile_id": "T00002", "region": "R00"}
    assert policy._tag_reports(read, 0.95, exact=True) == []
    assert policy._tag_reports(read, 0.76, exact=True) == [{"kind": "Reddening", "tile_id": "T00002"}]
    assert policy._tag_reports(dict(read, tile_id="T00003"), 1.42, exact=True) == [{"kind": "NOVA", "tile_id": "T00003"}]
    assert policy._tag_reports(dict(read, tile_id="T00004"), 0.76) == []
    assert policy._tag_reports(dict(read, tile_id="T00004"), 0.78) == [{"kind": "Reddening", "tile_id": "T00004"}]


def test_persistence_scales_future_nights_toward_the_mean():
    """天气持续性：今晚偏好时，之后各夜的价值系数逐夜回落到长期均值；水平等于均值时与不调整相同。"""
    from planner import Planner

    planner = Planner(initial(), dict(DEFAULTS))
    quantiles = planner._weather_quantiles()
    mean = sum(quantiles) / len(quantiles)
    assert planner._persistence_factors(quantiles, 0, 3) == {}
    planner.night_w[0] = [mean * 1.2]
    factors = planner._persistence_factors(quantiles, 0, 3)
    base = planner._value_factor(quantiles)
    assert factors[0](0.6) > factors[1](0.6) > factors[2](0.6) > base(0.6)
    planner.night_w[0] = [mean]
    assert planner._persistence_factors(quantiles, 0, 3)[2](0.6) == pytest.approx(base(0.6))


def test_recent_weather_only_conditions_nearby_clean_slots():
    from planner import Planner

    planner = Planner(initial(), dict(DEFAULTS, plan_recent_weather=True))
    quantiles = planner._weather_quantiles()
    now = planner.nights[0][0] + planner.slot
    mean = sum(quantiles) / len(quantiles)
    base = planner._value_factor(quantiles)
    snapshot = {"cursor": {"timestamp_utc": datetime.fromtimestamp(now, timezone.utc).isoformat()},
                "current_site_weather": {"slot_id": "s1", "is_observable": True,
                                         "transparency": mean * 1.2, "sky_quality": 1.0,
                                         "seeing_arcsec": 1.0, "active_event_ids": []}}
    planner.observe_weather(snapshot)
    bins = [b for b, (night, start, _stop, _samples) in enumerate(planner.bins)
            if night == 0 and now - 3600 < start < now + 3 * 3600]
    factors = planner._recent_weather_factors(quantiles, now, bins)
    assert factors and factors[bins[0]](0.6) > base(0.6)
    assert factors[bins[0]](0.6) > factors[bins[-1]](0.6)
    snapshot["current_site_weather"]["slot_id"] = "s2"
    snapshot["current_site_weather"]["active_event_ids"] = ["EV0001"]
    snapshot["current_site_weather"]["transparency"] = mean * 0.5
    planner.observe_weather(snapshot)
    assert planner.last_clean_weather[1] == pytest.approx(mean * 1.2)
    assert planner._recent_weather_factors(quantiles, now + 2 * 3600, bins) == {}


def test_clean_weather_quantiles_skip_site_events():
    from planner import Planner

    planner = Planner(initial(), dict(DEFAULTS, plan_clean_weather_quantiles=True))
    now = planner.nights[0][0] + planner.slot
    base = {"cursor": {"timestamp_utc": datetime.fromtimestamp(now, timezone.utc).isoformat()},
            "current_site_weather": {"slot_id": "s1", "is_observable": True,
                                     "transparency": 0.9, "sky_quality": 1.0,
                                     "seeing_arcsec": 1.0, "active_event_ids": ["EV0001"]}}
    planner.observe_weather(base)
    assert planner.w_samples == []
    assert planner.open_slots == 1
    base["current_site_weather"]["slot_id"] = "s2"
    base["current_site_weather"]["active_event_ids"] = []
    planner.observe_weather(base)
    assert planner.w_samples == [0.9]
    assert planner.last_clean_weather[1] == 0.9
    base["current_site_weather"]["slot_id"] = "s3"
    base["current_site_weather"]["transparency"] = 0.7
    planner.observe_weather(base)
    assert planner.w_samples == [0.7, 0.9]


def test_recent_weather_keeps_night_level_signal():
    from planner import Planner

    planner = Planner(initial(), dict(DEFAULTS, plan_recent_weather=True,
                                      plan_recent_weather_floor=0.82, plan_recent_weather_hours=10.0))
    quantiles = planner._weather_quantiles()
    mean = sum(quantiles) / len(quantiles)
    now = planner.nights[0][0] + planner.slot
    planner.last_clean_weather = (now, mean * 1.2, 0)
    bins = [b for b, (night, start, _stop, _samples) in enumerate(planner.bins)
            if night == 0 and now + 2 * 3600 < start < now + 5 * 3600]
    factor = planner._recent_weather_factors(quantiles, now, bins)
    base = planner._value_factor(quantiles)
    assert factor and all(factor[b](0.6) > base(0.6) for b in bins)
    assert factor[bins[0]](0.6) >= factor[bins[-1]](0.6)


def test_recent_weather_smoothing_uses_only_recent_clean_samples():
    from planner import Planner

    planner = Planner(initial(), dict(DEFAULTS, plan_recent_weather_smoothing_slots=3))
    quantiles = planner._weather_quantiles()
    mean = sum(quantiles) / len(quantiles)
    now = planner.nights[0][0] + 2 * planner.slot
    planner.recent_clean_weather = [(now - 4500, mean * 0.1, 0),
                                    (now - 1800, mean * 0.8, 0),
                                    (now - 900, mean, 0),
                                    (now, mean * 1.5, 0)]
    planner.last_clean_weather = (now, mean * 1.5, 0)
    bins = [b for b, (night, start, _stop, _samples) in enumerate(planner.bins)
            if night == 0 and now <= start < now + 3 * 3600]
    smoothed = planner._recent_weather_factors(quantiles, now, bins)
    planner.p["plan_recent_weather_smoothing_slots"] = 1
    single = planner._recent_weather_factors(quantiles, now, bins)
    assert smoothed
    close = [b for b in bins if planner.bins[b][1] - now < 3600]
    distant = [b for b in bins if planner.bins[b][1] - now >= 3600]
    assert close and distant
    assert all(smoothed[b](0.6) == pytest.approx(single[b](0.6)) for b in close)
    assert all(smoothed[b](0.6) < single[b](0.6) for b in distant)
    assert planner._recent_weather_factors(quantiles, now + 2 * 3600, bins) == {}


def test_value_factor_numpy_matches_python_fallback(monkeypatch):
    import planner as planner_module

    if planner_module.np is None:
        pytest.skip("numpy not installed")
    planner = planner_module.Planner(initial(), dict(DEFAULTS))
    quantiles = [0.4 + 0.025 * k for k in range(20)]
    vectorized = planner._value_factor(quantiles)
    monkeypatch.setattr(planner_module, "np", None)
    fallback = planner._value_factor(quantiles)
    for q in (0.0, 0.3, 0.6, 0.8, 1.0, 1.05):
        assert vectorized(q) == pytest.approx(fallback(q), abs=1e-12)


def test_recent_weather_conditional_reduces_distribution_spread(monkeypatch):
    from planner import Planner

    planner = Planner(initial(), dict(DEFAULTS, plan_recent_weather=True,
                                      plan_recent_weather_floor=0.82,
                                      plan_recent_weather_conditional=True))
    quantiles = planner._weather_quantiles()
    now = planner.nights[0][0] + planner.slot
    planner.last_clean_weather = (now, sum(quantiles) / len(quantiles), 0)
    monkeypatch.setattr(planner, "_value_factor", lambda samples: max(samples) - min(samples))
    bins = [b for b, (night, start, _stop, _samples) in enumerate(planner.bins)
            if night == 0 and now <= start < now + 3 * 3600]
    factors = planner._recent_weather_factors(quantiles, now, bins)
    assert factors and all(width < max(quantiles) - min(quantiles) for width in factors.values())


def test_current_bin_price_uses_only_remaining_geometry_samples():
    from planner import Planner

    planner = Planner(initial(), dict(DEFAULTS, plan_trim_current_bin=True))
    tid = "T00001"
    start = planner.bins[0][1]
    planner.qsamples[tid][0] = [(start + 450.0, 0.9), (start + 1350.0, 0.8),
                                (start + 2250.0, 0.7), (start + 3150.0, 0.6)]
    assert planner._remaining_bin_quality(tid, 0, start + 1800.0) == (0.7, start + 2250.0)
    assert planner._remaining_bin_quality(tid, 0, start + 3600.0) == (0.0, None)


def test_repeat_coverage_requires_higher_combined_score():
    policy = ObserverPolicy(initial(), dict(DEFAULTS, repeat_exact_coverage=True))
    policy.banked["T00001"] = 120.0
    policy.banked_base["T00001"] = 100.0
    policy.coverage_weight = 0.35
    assert policy._repeat_gain("T00001", 119.0, 110.0, 0.8) == (0.0, 0.0)
    science, coverage = policy._repeat_gain("T00001", 121.0, 98.0, 0.8)
    assert science == 1.0
    assert coverage == pytest.approx(-0.56)


def test_closure_risk_discounts_boundary_crossing():
    """跨时隙边界的曝光：下一时隙关闭就作废（已用时间白费、天区不消耗）；不跨边界不折算。"""
    policy = ObserverPolicy(initial(), dict(DEFAULTS, closure_risk=True, closure_prior=0.02))
    assert policy._risk_adjust(100.0, 900.0, [900.0]) == (100.0, 900.0)
    net, spent = policy._risk_adjust(100.0, 900.0, [300.0, 600.0])
    assert net == pytest.approx(98.0)
    assert spent == pytest.approx(0.02 * 300.0 + 0.98 * 900.0)


def test_closure_rate_counts_open_to_closed_within_a_night():
    policy = ObserverPolicy(initial(), dict(DEFAULTS, closure_risk=True))
    def step(slot, night, observable):
        policy._track_closures({"cursor": {"slot_id": slot, "night_id": night},
                                "current_site_weather": {"is_observable": observable}})
    step("N1-S001", "N1", True)
    step("N1-S001", "N1", True)    # 同一时隙的第二次决策不重复计数
    step("N1-S002", "N1", False)   # 开 → 关
    step("N2-S001", "N2", True)    # 跨夜不算转移
    step("N2-S002", "N2", True)
    assert (policy.open_transitions, policy.closures) == (2, 1)


def test_budget_steps_down_gently_and_reprojects(monkeypatch):
    """推算耗时超过 50%：先把半小时重规划退回每小时；之后按降级以来的速度外推，够用就不再降级。"""
    import policy as policy_module

    clock = {"t": 0.0}
    monkeypatch.setattr(policy_module.time, "monotonic", lambda: clock["t"])
    agent = ObserverPolicy(initial(), dict(DEFAULTS, plan_every_seconds=1800))
    start, end = agent.survey_span
    agent._budget(start)                                  # 开始计时
    clock["t"] = 0.10 * 3600 * 0.80                       # 开局开销大：进度 10% 时推算 80%，但第一次检查只防时钟耗尽
    agent._budget(start + 0.10 * (end - start))
    assert (agent.frequent, agent.hourly, agent.planning) == (True, True, True)
    clock["t"] += 0.10 * 3600 * 0.55                      # 此后按 55% 的速度 → 退回每小时重规划
    agent._budget(start + 0.20 * (end - start))
    assert (agent.frequent, agent.hourly, agent.planning) == (False, True, True)
    clock["t"] += 0.10 * 3600 * 0.40                      # 降级后按 40% 的速度走完下一个 10%：不再降级
    agent._budget(start + 0.30 * (end - start))
    assert (agent.frequent, agent.hourly, agent.planning) == (False, True, True)
    hourly = ObserverPolicy(initial(), dict(DEFAULTS, plan_every_seconds=3600))
    clock["t"] = 0.0
    hourly._budget(start)
    clock["t"] = 0.10 * 3600 * 0.55
    hourly._budget(start + 0.10 * (end - start))
    clock["t"] += 0.10 * 3600 * 0.55
    hourly._budget(start + 0.20 * (end - start))
    assert (hourly.frequent, hourly.hourly) == (True, True)  # 间隔本来就是 1 小时：55% 不触发任何降级
    clock["t"] += 0.10 * 3600 * 0.70
    hourly._budget(start + 0.30 * (end - start))
    assert hourly.hourly is False


def test_numpy_planner_matches_pure_python_bit_for_bit():
    """真实决赛规模场景的首次规划（300 次迭代）：numpy 与纯 Python 两条路径的 μ 逐位相同。"""
    import planner as planner_module

    if planner_module.np is None:
        pytest.skip("numpy not installed")
    sys.path.insert(0, str(ROOT / "kit"))
    from challenge.challenge_workflow import ChallengeWorkflow

    scenario = ROOT / "scenarios" / "comp-a-s101"
    if not scenario.exists():
        pytest.skip("local scenario not generated")
    initial = ChallengeWorkflow(root=scenario).initial_publication()
    now = epoch(initial["calendar"]["first_night"] + "T02:00:00Z")
    coverage = (0.35, 0.0, {r: 0 for r in initial["tile_catalog"]["region_ids"]})
    for average in (0, 50):  # 也覆盖“最后 50 次迭代取平均 λ”
        results = []
        for flag in (True, False):
            planner = planner_module.Planner(initial, dict(DEFAULTS, numpy_planner=flag, plan_lambda_average=average))
            planner.replan(now, set(), set(), coverage)
            results.append(planner.mu)
        assert results[0] and results[0] == results[1]


def test_cold_wave_reads_are_normalized_by_the_event_multiplier(monkeypatch):
    """寒潮只压低效率：同一事件此前读数的中位数 / 0.95 估计乘数，除掉后按精确口径判断标签。"""
    policy = ObserverPolicy(initial(), dict(DEFAULTS))
    monkeypatch.setattr(policy, "_posterior_expected", lambda *args, **kwargs: 100.0)
    snap = {"current_site_weather": {"active_event_ids": ["EV0007"]}}
    def read(tile, realized):
        pending = {"tile_id": tile, "region": "R00", "site_event_ids": ["EV0007"]}
        return policy._cold_evidence(pending, snap, 0.0, realized)
    cold = 0.75
    for i in range(3):                                   # 先验读数：普通天区
        assert read(f"T0000{i + 1}", 100.0 * cold * 0.95) == []
    assert read("T00004", 100.0 * cold * 0.95 * 0.8) == [{"kind": "Reddening", "tile_id": "T00004"}]
    assert read("T00005", 100.0 * cold * 0.95 * 1.45) == [{"kind": "NOVA", "tile_id": "T00005"}]
    assert read("T00006", 100.0 * cold * 0.97) == []


def run_agent(lines: list) -> tuple[list, str]:
    """把若干行（dict 或原始字符串）喂给 agent 进程，返回 (解析后的响应列表, stderr)。"""
    text = "".join((line if isinstance(line, str) else json.dumps(line)) + "\n" for line in lines)
    proc = subprocess.run([sys.executable, str(ROOT / "agents" / "observer" / "main.py")], input=text,
                          capture_output=True, text=True, timeout=60, check=True)
    return [json.loads(row) for row in proc.stdout.splitlines() if row.strip()], proc.stderr


def request(sequence: int, version: str = "participant-agent-protocol-v2", schema: str = "decision-snapshot-v3") -> dict:
    payload = dict(snapshot([candidate("T00001", "R01", 1.2)]), decision_sequence=sequence, schema_version=schema)
    return {"protocol_version": version, "message_type": "decision_request", "decision_sequence": sequence, "payload": payload}


INIT = {"protocol_version": "participant-agent-protocol-v2", "message_type": "initialize", "payload": initial()}


def test_unknown_messages_and_versions_never_stop_the_agent():
    """不认识的消息类型跳过；不认识的协议 / schema 版本照常决策并回显协议版本；进程不退出。"""
    heartbeat = {"protocol_version": "participant-agent-protocol-v2", "message_type": "heartbeat", "decision_sequence": 9}
    responses, stderr = run_agent([dict(INIT, payload=dict(initial(), schema_version="initial-publication-v9")),
                                   request(1), heartbeat,
                                   request(2, version="participant-agent-protocol-v3", schema="decision-snapshot-v9")])
    assert [r["decision_sequence"] for r in responses] == [1, 2]
    assert responses[0]["protocol_version"] == "participant-agent-protocol-v2"
    assert responses[1]["protocol_version"] == "participant-agent-protocol-v3"
    assert all(r["action"] == "observe" for r in responses)
    assert "Traceback" not in stderr and "heartbeat" in stderr and "decision-snapshot-v9" in stderr


def test_repeated_sequence_replays_the_first_response_verbatim():
    responses, _ = run_agent([INIT, request(1), request(1), request(2)])
    assert [r["decision_sequence"] for r in responses] == [1, 1, 2]
    assert responses[0] == responses[1]


def test_unreadable_request_is_answered_with_wait():
    """JSON 坏掉的请求：能从原文认出序号就回等待，否则跳过；之后的请求照常处理。"""
    broken = json.dumps(request(2))[:300]
    responses, stderr = run_agent([INIT, request(1), broken, "not json at all", request(3)])
    assert [(r["decision_sequence"], r["action"]) for r in responses] == [(1, "observe"), (2, "wait"), (3, "observe")]
    assert "unreadable message" in stderr


def test_request_before_initialize_uses_the_fallback():
    responses, _ = run_agent([request(1, version="participant-agent-protocol-v1", schema="decision-snapshot-v2")])
    assert responses[0]["decision_sequence"] == 1 and responses[0]["action"] == "observe"
    assert responses[0]["protocol_version"] == "participant-agent-protocol-v1"


def test_unknown_snapshot_schema_follows_the_scoring_mechanics():
    """快照 schema 不认识时，是否启用异常机制按评分配置里有没有异常机制小节决定（已知版本不受影响）。"""
    plain = ObserverPolicy(initial(), dict(DEFAULTS))
    anomalies = ObserverPolicy(dict(initial(), scoring_contract=dict(CONTRACT, score_config=dict(SCORE_CONFIG, reporting={}))),
                               dict(DEFAULTS))
    assert not plain.mechanics and anomalies.mechanics


def test_cold_factor_uses_clean_reads_then_forecast_then_default():
    """寒潮期在线估值乘数：读数（不含故障分区）中位数 / 0.95 → 预报 severity → 默认 0.75；站点没有生效事件时为 1。"""
    policy = ObserverPolicy(initial(), dict(DEFAULTS, cold_online=True))
    snap = snapshot([candidate("T00001", "R01", 1.2)])
    now = epoch(snap["cursor"]["timestamp_utc"])
    assert policy._cold_factor(snap, now) == 1.0
    snap["current_site_weather"]["active_event_ids"] = ["EV0026"]
    assert policy._cold_factor(snap, now) == DEFAULTS["cold_default_factor"]
    policy.forecasts = [{"condition": "cold_wave", "predicted_start_utc": "2026-10-06T00:00:00Z",
                         "predicted_end_utc": "2026-10-07T00:00:00Z", "severity": 0.5,
                         "start_uncertainty_seconds": 3600, "end_uncertainty_seconds": 3600}]
    assert abs(policy._cold_factor(snap, now) - 0.84) < 1e-12
    policy.cold_clean["EV0026"] = [0.70, 0.72, 0.74]
    assert abs(policy._cold_factor(snap, now) - 0.72 / 0.95) < 1e-12


def test_forecast_closure_risk_only_when_rain_starts_at_the_boundary():
    """按预报的中断风险：降雨预计在曝光跨过的边界处开始时风险明显（启用折算）；没有预报时不折算。"""
    import events as events_mod
    from planner import Planner
    policy = ObserverPolicy(initial(), dict(DEFAULTS, closure_risk_forecast=True))
    start = epoch("2026-10-06T03:00:00Z")
    assert policy._forecast_risks(start, [450.0, 450.0]) is None  # 还没有规划器 / 预报
    policy.planner = Planner(policy.initial, policy.p)
    rain = events_mod.Event(start + 450.0, start + 4 * 3600.0, 900.0, 0.9, "ALL", {}, True, 0.1, 1.0)
    policy.planner.set_events([rain], "forecast", start + 7 * 86400.0)
    risks = policy._forecast_risks(start, [450.0, 450.0])
    assert risks is not None and risks[0] > 0.3
    policy.planner.set_events([], "forecast", start + 7 * 86400.0)
    assert policy._forecast_risks(start, [450.0, 450.0]) is None


def test_planner_prices_repeats_only_for_underbanked_tiles():
    """复拍定价：入账分远低于潜力的已完成天区得到正的机会价值；入账分已经很高的不参与；不给 repeats 时已完成天区不定价。"""
    from planner import Planner
    params = dict(DEFAULTS, plan_repeats=True)
    now = epoch("2026-10-06T02:00:00Z")
    planner = Planner(initial(), params)
    planner.replan(now, {"T00001"}, set(), repeats={"T00001": (1.0, 1.0)})
    assert planner.mu.get("T00001", 0.0) > 0.0
    planner.replan(now, {"T00001"}, set(), repeats={"T00001": (1e6, 1.0)})
    assert "T00001" not in planner.mu
    planner.replan(now, {"T00001"}, set())
    assert "T00001" not in planner.mu
