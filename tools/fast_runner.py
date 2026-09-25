#!/usr/bin/env python3
"""本地评测用的加速 runner：缓存及批算官方 kit 的纯计算，然后调用 kit/local_runner.py 的 main()。

    uv run python tools/fast_runner.py --scenario scenarios/comp-a-s101 --agent agents/observer/main.py \
        --out runs/x --no-replay --quiet                    # 参数与 kit/local_runner.py 完全相同

- kit 文件一行不改，补丁只在本进程内生效。NumPy 可用时对一时刻的全部天区批算几何；
  浮点末位可能与官方 runner 不同，需用多场景评分对照验证。没有 NumPy 时退回纯函数缓存路径。
- 启动时校验 kit 全部源文件的 SHA-256，与编写补丁时的版本不一致就拒绝运行：kit 更新后要重新核对补丁、
  用 --verify 复核，再更新 KIT_SHA256。orjson 可用时仅加速本地 agent 协议编码。
- --direct 是只供本地实验使用的 observer 同进程模式，省去大快照的 JSON 往返；正式协议验证仍用默认模式。

补丁：
  1. geometry_sample：恒星时、太阳与月亮位置、日月角距、月亮高度只与时刻有关，按时刻缓存（原代码对每块天区重算，
     还要用 dataclasses.replace 造一个“月亮天区”）。
  2. get_tile_windows：按夜缓存（当夜发布与每周发布会重复计算同一夜），每次返回各行的浅拷贝。
  3. Slot / WeatherSlot.end_utc：冻结 dataclass 上的派生属性，缓存到实例。
  4. get_effective_conditions：同一时隙内“按时间生效的事件”与时隙天气字典对所有天区相同，按时隙缓存；
     缓存键带上故障修复写入的 end_overrides，修复后自动换键。运行中 weather.events 不会被修改。
  5. format_utc：按时刻缓存（只接受带时区的时刻，结果只取决于时刻本身；无时区照旧报错、不缓存）。
  6. decision_snapshot 每次决策都把当夜全部窗口的起止时刻 fromisoformat 解析一遍：按字符串缓存。
     （challenge_workflow 里 datetime 只用于这两处解析与类型注解）。
  7. 窗口生成、决策快照的天区几何按时刻交给 NumPy 批算；窗口分组及结果结构仍由 kit 原函数生成。
  8. agent 的大快照用 orjson 编码；协议写入、超时和读取仍用 kit 原函数。
"""
from __future__ import annotations

import hashlib
import importlib
import math
import os
import sys
from contextlib import contextmanager, redirect_stderr
from dataclasses import replace
from datetime import datetime, timedelta
from functools import cached_property
from pathlib import Path

try:
    import numpy as np  # noqa: F401 - optional vector geometry dependency
except ImportError:
    BatchGeometry = None
else:
    if __package__:
        from .vector_geometry import BatchGeometry
    else:
        from vector_geometry import BatchGeometry

try:
    import orjson
except ImportError:
    orjson = None

KIT = Path(__file__).resolve().parent.parent / "kit"
KIT_SHA256 = {
    "local_runner.py": "f4fbf5405e067adaa326c3fe5306c83d9df893d18733af5d954742f316eb506a",
    "challenge/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "challenge/build_scenario_manifest.py": "cd9b29fa2b682bb9140ba2e02dab3d15529184309244983fd092ede85ef4b41f",
    "challenge/challenge_workflow.py": "55e49a9fe79f9d671a69ca97d5ecf2eee4c0a316e1fd2a7d41e859152d6a06ed",
    "challenge/contracts.py": "68d3bb6b4b8e18cf746acc8a6b51f3b38d5302bd34b5d295dc9e930358c25b0a",
    "challenge/observation_request_simulator.py": "62b83c44523f787814387da20f3c7ba993b299a7c1a631cb46184b8a4f768403",
    "challenge/observing_calendar.py": "897f81e4c0fd3b8eca5a6d490d96aff2e9ff2b6ca4a1d0b6837ee9a479125117",
    "challenge/project_paths.py": "0bde3926c12a89d48694bcf8ec78c1a915936d5a02b647004184ac8887df6857",
    "challenge/replay.py": "521550de3b6086967c33d09b98b944ca96b13a2c9d814486b74fb75472391e6e",
    "challenge/run_challenge.py": "afbdbea372a9a52d84e42016f12d3614d84b3bf695675d3acee7fd28957b0eac",
    "challenge/scenario_builder.py": "2c23985b6e1c0d0802309ea9c67ca724e46aa773db665efaa493916ee27453dd",
    "challenge/score_decisions.py": "66b5f29c09ac60e4020082bc6b8277ba61498b9d0ba2d30f9a8ebeee215b874d",
    "challenge/scoring_core.py": "abb3e2758b11380f1b894d8eb743a220e4384f0b55f964cac51a6d19eca93b65",
    "challenge/scoring_preview.py": "368bd044f84cabb94342d6a85e020e119362197c2d1a2f6cbd20ddb694f80a5a",
    "challenge/tile_geometry_simulator.py": "8a86c65beb6ce9e27aee7ba2e2c1cda2a5bd3c59cb662a3c77cebf44dedefd30",
    "challenge/weather_simulator.py": "80ed0dc912def1b54b911c7b8994101655923764d2d185b1b10598420ec46e88",
}


def verify_kit() -> None:
    files = {path.relative_to(KIT).as_posix() for path in [KIT / "local_runner.py", *(KIT / "challenge").glob("*.py")]}
    changed = sorted(name for name in files | set(KIT_SHA256)
                     if not (KIT / name).is_file() or hashlib.sha256((KIT / name).read_bytes()).hexdigest() != KIT_SHA256.get(name))
    if changed:
        raise SystemExit(f"fast_runner: kit 文件与编写补丁时不一致：{changed}。请重新核对补丁、用 evaluate.py --verify 复核后更新 KIT_SHA256，"
                         "或改用官方 kit/local_runner.py")


def apply_patches() -> None:
    import challenge.challenge_workflow as cw
    import challenge.observing_calendar as calendar
    import challenge.tile_geometry_simulator as tgs
    import challenge.weather_simulator as ws
    from challenge import contracts

    # 1. geometry_sample：按时刻缓存与天区无关的量 -----------------------------------------------------------
    moment_terms: dict = {}
    active_batches: dict = {}

    @contextmanager
    def using_batches(batches):
        previous = active_batches.copy()
        active_batches.update(batches)
        try:
            yield
        finally:
            active_batches.clear()
            active_batches.update(previous)

    def vector_for(simulator):
        vector = simulator.__dict__.get("_fast_vector")
        if vector is None:
            vector = BatchGeometry(simulator)
            simulator.__dict__["_fast_vector"] = vector
        return vector

    def batch_at(simulator, moment):
        vector = vector_for(simulator)
        terms = terms_at(vector.tiles[0], moment, simulator.tile_config, simulator.calendar_config)
        return vector, vector.at(terms)

    def terms_at(tile, moment, tile_config, calendar_config):
        key = (moment, id(tile_config), id(calendar_config))
        terms = moment_terms.get(key)
        if terms is None:
            sidereal = tgs._local_sidereal_deg(moment, float(calendar_config["site"]["longitude_deg"]))
            sun_ra, sun_dec = tgs._sun_equatorial_deg(moment)
            moon_ra, moon_dec = tgs._moon_equatorial_deg(moment)
            sun_moon_separation = tgs._angular_separation_deg(sun_ra, sun_dec, moon_ra, moon_dec)
            illumination = (1.0 - math.cos(math.radians(sun_moon_separation))) / 2.0
            moon_tile = replace(tile, ra_deg=moon_ra, dec_deg=moon_dec)  # 月亮高度只用到赤经赤纬，与借用哪块天区无关
            moon_altitude = tgs.geometry_sample_without_lunar(moon_tile, moment, calendar_config)["altitude_deg"]
            lunar = tile_config["lunar_model"]
            altitude_weight = math.sin(math.radians(max(0.0, moon_altitude))) ** float(lunar["altitude_exponent"])
            # 原式 1.0 − max_penalty·illumination·altitude_weight·angular_weight 从左到右结合，前三项的积可以预先算好
            terms = (sidereal, moon_ra, moon_dec, float(lunar["maximum_penalty"]) * illumination * altitude_weight,
                     float(lunar["angular_decay_scale_deg"]))
            moment_terms[key] = terms
        return terms

    def geometry_sample(tile, moment, tile_config, calendar_config):
        batch = active_batches.get(moment)
        if batch is not None:
            vector, values = batch
            index = vector.index.get(id(tile))
            if index is not None:
                return vector.sample(values, index)
        sidereal, moon_ra, moon_dec, lunar_scale, decay_scale = terms_at(tile, moment, tile_config, calendar_config)
        site = calendar_config["site"]
        latitude = math.radians(float(site["latitude_deg"]))
        declination = math.radians(tile.dec_deg)
        hour_angle_deg = (sidereal - tile.ra_deg + 180.0) % 360.0 - 180.0
        hour_angle = math.radians(hour_angle_deg)
        sin_altitude = (
            math.sin(latitude) * math.sin(declination)
            + math.cos(latitude) * math.cos(declination) * math.cos(hour_angle)
        )
        altitude = math.asin(max(-1.0, min(1.0, sin_altitude)))
        cos_altitude = max(1e-12, math.cos(altitude))
        sin_azimuth = -math.sin(hour_angle) * math.cos(declination) / cos_altitude
        cos_azimuth = (
            math.sin(declination) - math.sin(altitude) * math.sin(latitude)
        ) / (cos_altitude * max(1e-12, math.cos(latitude)))
        azimuth = math.degrees(math.atan2(sin_azimuth, cos_azimuth)) % 360.0
        altitude_deg = math.degrees(altitude)
        tile_moon_separation = tgs._angular_separation_deg(tile.ra_deg, tile.dec_deg, moon_ra, moon_dec)
        angular_weight = math.exp(-tile_moon_separation / decay_scale)
        lunar_quality = 1.0 - lunar_scale * angular_weight
        return {
            "altitude_deg": altitude_deg,
            "azimuth_deg": azimuth,
            "hour_angle_deg": hour_angle_deg,
            "airmass": tgs._normalized_airmass(altitude_deg),
            "moon_separation_deg": tile_moon_separation,
            "lunar_quality_factor": max(0.0, min(1.0, lunar_quality)),
        }

    tgs.geometry_sample = geometry_sample

    # 2. get_tile_windows：按夜缓存，返回浅拷贝 ---------------------------------------------------------------
    original_windows = tgs.TileGeometrySimulator.get_tile_windows

    def get_tile_windows(self, first_night, days=1):
        if days < 1:
            raise ValueError("days must be positive")
        night_ids = [f"N{(first_night + timedelta(days=offset)).strftime('%Y%m%d')}" for offset in range(days)]
        missing = [night_id for night_id in night_ids if night_id not in self.nights]
        if missing:
            raise ValueError(f"requested nights outside calendar: {missing}")
        cache = self.__dict__.setdefault("_fast_windows", {})
        rows = []
        for offset, night_id in enumerate(night_ids):
            if night_id not in cache:
                if BatchGeometry is None or os.environ.get("FAST_RUNNER_VECTOR") == "0":
                    cache[night_id] = original_windows(self, first_night + timedelta(days=offset), 1)
                else:
                    batches = {
                        slot.timestamp_utc + timedelta(seconds=slot.duration_seconds / 2):
                        batch_at(self, slot.timestamp_utc + timedelta(seconds=slot.duration_seconds / 2))
                        for slot in self.slots_by_night[night_id]
                    }
                    with using_batches(batches):
                        cache[night_id] = original_windows(self, first_night + timedelta(days=offset), 1)
            rows.extend(dict(row) for row in cache[night_id])
        return rows

    tgs.TileGeometrySimulator.get_tile_windows = get_tile_windows

    # 3. end_utc：缓存到实例（cached_property 直接写实例 __dict__，冻结 dataclass 也可用） ---------------------
    for cls in (calendar.Slot, ws.WeatherSlot):
        prop = cached_property(lambda self: self.timestamp_utc + timedelta(seconds=self.duration_seconds))
        prop.__set_name__(cls, "end_utc")
        cls.end_utc = prop

    # 4. get_effective_conditions：按时隙缓存按时间生效的事件与时隙天气字典 -------------------------------------
    def get_effective_conditions(self, slot_id, tile_id=None, *, include_instrument_faults=True):
        if slot_id not in self._weather:
            raise ValueError(f"unknown slot_id {slot_id!r}")
        base = self._weather[slot_id]
        cache = self.__dict__.setdefault("_fast_conditions", {})
        key = (slot_id, tuple(sorted(self.end_overrides.items())))
        entry = cache.get(key)
        if entry is None:
            entry = ([event for event in self.events if self._event_active(event, base)], base.public_dict())
            cache[key] = entry
        timed, public = entry
        active = [event for event in timed if (include_instrument_faults or event.condition != "instrument_fault")
                  and (event.spatial_scope_type == "ALL" or (tile_id is not None and self._applies(event, tile_id, base)))]
        payload = dict(public)
        payload["tile_id"] = tile_id
        payload["active_event_ids"] = [event.event_id for event in active]
        if not base.is_observable:
            return payload
        directional = [event for event in active if event.spatial_scope_type != "ALL"]
        if any(event.force_close for event in directional):
            payload.update({"is_observable": False, "seeing_arcsec": None, "transparency": None, "sky_quality": None, "instrument_efficiency": None})
            return payload
        multipliers = {"seeing_arcsec": 1.0, "transparency": 1.0, "sky_quality": 1.0, "instrument_efficiency": 1.0}
        for event in directional:
            multipliers["seeing_arcsec"] *= event.seeing_multiplier
            multipliers["transparency"] *= event.transparency_multiplier
            multipliers["sky_quality"] *= event.sky_quality_multiplier
            multipliers["instrument_efficiency"] *= event.instrument_efficiency_multiplier
        for name, multiplier in multipliers.items():
            payload[name] = ws._clip(float(payload[name]) * multiplier, self.config["quality"][name])
        return payload

    ws.WeatherSimulator.get_effective_conditions = get_effective_conditions

    # 5. format_utc：按时刻缓存，替换所有已导入模块里的引用 -----------------------------------------------------
    original_format = contracts.format_utc
    formatted: dict = {}

    def format_utc(value):
        text = formatted.get(value)
        if text is None:
            text = original_format(value)  # 无时区的时刻在这里照旧报错
            formatted[value] = text
        return text

    for name, module in list(sys.modules.items()):
        if (name == "local_runner" or name.startswith("challenge")) and getattr(module, "format_utc", None) is original_format:
            module.format_utc = format_utc

    # 6. decision_snapshot 里的窗口时刻解析：按字符串缓存 ------------------------------------------------------
    parsed: dict = {}

    class CachedDatetime:
        @staticmethod
        def fromisoformat(text):
            value = parsed.get(text)
            if value is None:
                value = datetime.fromisoformat(text)
                parsed[text] = value
            return value

    cw.datetime = CachedDatetime

    # 7. 决策快照：原逻辑保留，仅让其逐天区几何调用从批算结果读取 ----------------------------------------------
    original_snapshot = cw.ChallengeWorkflow.decision_snapshot

    def decision_snapshot(self, sequence):
        if BatchGeometry is None or os.environ.get("FAST_RUNNER_VECTOR") == "0":
            return original_snapshot(self, sequence)
        moment = self.scorer.current_time()
        if moment is None:
            return original_snapshot(self, sequence)
        with using_batches({moment: batch_at(self.scorer.geometry, moment)}):
            return original_snapshot(self, sequence)

    cw.ChallengeWorkflow.decision_snapshot = decision_snapshot

    # 8. 只替换协议模块的 JSON 编码器，继续使用 kit 的非阻塞写入与超时处理 -------------------------------
    if orjson is not None and os.environ.get("FAST_RUNNER_ORJSON") != "0":
        import json
        from types import SimpleNamespace

        from challenge import run_challenge

        def dumps(value, *args, **kwargs):
            if not args and kwargs == {"separators": (",", ":"), "ensure_ascii": False}:
                return orjson.dumps(value).decode("utf-8")
            return json.dumps(value, *args, **kwargs)

        run_challenge.json = SimpleNamespace(dumps=dumps, loads=json.loads)


def enable_direct_observer(local_runner) -> None:
    class DirectObserver:
        def __init__(self, command, *, agent_dir, env, stderr, initialization_timeout_seconds=30.0,
                     protocol_version=None):
            if Path(command[-1]).name != "main.py" or not (agent_dir / "policy.py").is_file():
                raise ValueError("--direct only supports the observer main.py agent")
            if Path(command[0]).resolve() != Path(sys.executable).resolve():
                raise ValueError("--direct requires the runner and agent to use the same Python interpreter")
            self.agent_dir = agent_dir
            self.env = env
            self.stderr = stderr
            self.protocol_version = protocol_version
            self.policy = None
            self.agent_main = None
            self._previous_environment = None

        def _activate_environment(self):
            if self._previous_environment is None:
                self._previous_environment = dict(os.environ)
                os.environ.clear()
                os.environ.update(self.env)

        def _restore_environment(self):
            if self._previous_environment is not None:
                os.environ.clear()
                os.environ.update(self._previous_environment)
                self._previous_environment = None

        def publish_initial(self, publication):
            self._activate_environment()
            try:
                with redirect_stderr(self.stderr or sys.stderr):
                    sys.path.insert(0, str(self.agent_dir))
                    self.agent_main = importlib.import_module("main")
                    params = self.agent_main.load_params(self.agent_dir)
                    initial = dict(publication)
                    initial.pop("target_catalog", None)
                    self.policy = self.agent_main.ObserverPolicy(initial, params)
            except Exception:
                self._restore_environment()
                raise

        def __call__(self, snapshot, deadline_monotonic):
            with redirect_stderr(self.stderr or sys.stderr):
                try:
                    decision = self.policy.decide(snapshot)
                except Exception:  # noqa: BLE001 - mirror the agent's policy-error fallback
                    import traceback

                    traceback.print_exc(file=sys.stderr)
                    decision = self.agent_main.fallback(snapshot, self.policy)
                response = self.agent_main.decision_response(snapshot["decision_sequence"], decision,
                                                              decision.get("reports"))
                response["protocol_version"] = self.protocol_version
                return response

        def close(self, force=False):
            self._restore_environment()

    local_runner.LocalAgentProcess = DirectObserver


def main(argv=None) -> int:
    verify_kit()
    sys.path.insert(0, str(KIT))
    import local_runner  # 必须在校验之后导入

    apply_patches()
    args = list(sys.argv[1:] if argv is None else argv)
    if "--direct" in args:
        args.remove("--direct")
        enable_direct_observer(local_runner)
    return local_runner.main(args)


if __name__ == "__main__":
    raise SystemExit(main())
