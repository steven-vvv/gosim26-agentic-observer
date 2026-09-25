"""agents/observer 的单元测试：与官方 kit 口径一致性、覆盖边际方向、协议往返。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
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
