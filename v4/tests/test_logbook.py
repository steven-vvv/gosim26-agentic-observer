"""The duty-log reader acts only on long request texts and reports the faults the log names."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

import numpy as np
import pytest

from v4.agent.observer4.config import load
from v4.agent.observer4.geometry import parse_utc
from v4.agent.observer4.logbook import Logbook, decode_log
from v4.agent.observer4.policy import Observer
from v4.agent.observer4.services import load_client
from v4.agent.observer4.terrain import TerrainModel
from v4.tests.test_runtime import ROOT, publication  # noqa: F401 - pytest fixture

CONFIG_PATH = ROOT / "v4" / "agent" / "config.json"

LOG = "【立希】从昨晚 2点45 左右，吞吐量只剩 4成左右，没回来过。allsky 黑的，星点不胖。不关天的事，提单，别拖。"


def fake_model(monkeypatch, reading):
    client = load_client("model_advisor")
    calls = []

    def transport(request, *, timeout):
        body = json.loads(request.data)
        calls.append(json.loads(body["messages"][1]["content"]))
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": json.dumps(reading)}}]}).encode())

    monkeypatch.setenv("OPENAI_BASE_URL", "http://model.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(client.urllib.request, "urlopen", transport)
    return calls


def book(config=None):
    events = []
    reader = Logbook(SimpleNamespace(longitude=-155.0), {"site": {"utc_offset_hours": -10.0}},
                     config or load(CONFIG_PATH, {"logbook": {"rules": False}}),
                     lambda kind, payload: events.append((kind, payload)))
    return reader, events


def request(text, issued="2026-12-27T04:34:00Z"):
    return {"record_type": "observation_request", "issued_at_utc": issued, "reason": text, "completion_reward": 0.0}


def test_reader_reads_each_long_text_once_and_dates_last_night(monkeypatch):
    calls = fake_model(monkeypatch, {"now": [{"time": "2026-12-26 02:45", "zone": "local"}], "reason": "hardware"})
    reader, _ = book()
    reader.collect([request(LOG), request(LOG), request("time-critical follow-up")])
    reader.process(1)
    assert len(calls) == 1 and calls[0]["log"] == LOG and calls[0]["site_utc_offset_hours"] == -10.0
    # 02:45 local at UTC-10 is 12:45 UTC.
    assert reader.faults[0]["start"] == parse_utc("2026-12-26T12:45:00Z").timestamp()


def test_an_appointment_is_due_exactly_at_its_time(monkeypatch):
    # The engineers announce each guide-camera move ahead of time; the report is due at that
    # moment, so a time after the log is kept as given.
    fake_model(monkeypatch, {"appointments": [{"time": "2026-12-26 21:30", "zone": "local"},
                                              {"time": "2026-12-27 07:30", "zone": "utc"}]})
    reader, _ = book()
    reader.collect([request(LOG, issued="2026-12-27T04:34:00Z")])  # 18:34 local on 12-26
    reader.process(1)
    # The local and the UTC wording name the same move: one fault.
    assert [item["start"] for item in reader.faults] == [parse_utc("2026-12-27T07:30:00Z").timestamp()]
    assert reader.faults[0]["future"] is True
    assert reader.due(parse_utc("2026-12-27T07:29:00Z").timestamp(), None) is None
    assert reader.due(parse_utc("2026-12-27T07:30:00Z").timestamp(), None) is reader.faults[0]


def test_a_stated_foreign_clock_is_converted_in_code():
    reader, _ = book(load(CONFIG_PATH, {"logbook": {"enabled": False}}))
    issued = parse_utc("2026-10-02T00:00:00Z").timestamp()
    reader._apply({"appointments": [{"time": "2026-10-04 12:15", "zone": "UTC+9"},
                                    {"time": "2026-10-05 01:00", "zone": "UTC-11"}]}, issued)
    assert [item["start"] for item in reader.faults] == [parse_utc("2026-10-04T03:15:00Z").timestamp(),
                                                         parse_utc("2026-10-05T12:00:00Z").timestamp()]


def test_a_cancelled_appointment_is_never_reported():
    reader, events = book(load(CONFIG_PATH, {"logbook": {"enabled": False}}))
    issued = parse_utc("2027-06-08T16:18:00Z").timestamp()
    reader._apply({"appointments": [{"time": "2027-06-10 22:45", "zone": "local"}]}, issued)
    reader._apply({"cancelled": [{"time": "2027-06-10 22:45", "zone": "local"}]}, issued)
    assert reader.due(parse_utc("2027-06-12T12:00:00Z").timestamp(), None) is None
    # A later restatement of the cancelled time does not bring it back.
    reader._apply({"appointments": [{"time": "2027-06-10 22:45", "zone": "local"}]}, issued + 600)
    assert reader.due(parse_utc("2027-06-12T12:00:00Z").timestamp(), None) is None
    assert [kind for kind, _ in events] == ["logbook_fault", "logbook_cancel"]


def test_quiet_windows_and_the_next_boundary():
    reader, _ = book(load(CONFIG_PATH, {"logbook": {"enabled": False}}))
    issued = parse_utc("2027-06-01T07:15:00Z").timestamp()
    reader._apply({"quiet": [{"start": "2027-06-01 21:45", "end": "2027-06-01 22:00", "zone": "local"}],
                   "appointments": [{"time": "2027-06-02 00:30", "zone": "local"}]}, issued)
    low, high = parse_utc("2027-06-02T07:45:00Z").timestamp(), parse_utc("2027-06-02T08:00:00Z").timestamp()
    assert reader.quiet == [(low, high)]
    assert reader.in_quiet(low - 600, low + 60) and not reader.in_quiet(high, high + 600)
    assert reader.next_boundary(low - 3600) == low
    assert reader.next_boundary(high) == parse_utc("2027-06-02T10:30:00Z").timestamp()


def test_an_implausibly_distant_time_is_ignored():
    reader, _ = book(load(CONFIG_PATH, {"logbook": {"enabled": False}}))
    issued = parse_utc("2027-06-08T16:18:00Z").timestamp()
    reader._apply({"appointments": [{"time": "2028-06-10 22:45", "zone": "local"}],
                   "now": [{"time": "bad", "zone": "local"}]}, issued)
    assert reader.faults == []


def test_due_skips_a_fault_that_a_later_correct_report_repaired():
    reader, _ = book(load(CONFIG_PATH, {"logbook": {"enabled": False}}))
    reader._apply({"now": [{"time": "2026-12-26 02:45", "zone": "local"}]},
                  parse_utc("2026-12-27T04:34:00Z").timestamp())
    start = reader.faults[0]["start"]
    assert reader.due(start + 7200, start + 3600) is None
    assert reader.faults[0]["state"] == "covered"
    reader._apply({"appointments": [{"time": "2027-01-16 22:15", "zone": "local"}]},
                  parse_utc("2027-01-17T07:15:00Z").timestamp())
    item = reader.due(parse_utc("2027-01-17T10:00:00Z").timestamp(), start + 3600)
    assert item is reader.faults[1]
    reader.reported(item, 0.0)
    reader.outcome(False)
    assert reader.faults[1]["state"] == "false" and reader.due(1e12, None) is None


def test_a_name_only_one_reader_finds_waits_for_a_free_report():
    reader, _ = book(load(CONFIG_PATH, {"logbook": {"enabled": False}}))
    issued = parse_utc("2027-06-01T17:15:00Z").timestamp()
    both = parse_utc("2027-06-01T18:30:00Z").timestamp()
    alone = parse_utc("2027-06-02T18:45:00Z").timestamp()
    reader.read = {"model": {"t1"}, "rules": {"t1"}}
    reader._merge([both, alone], [], [], [], issued, "model", "t1", "")
    reader._merge([both], [], [], [], issued, "rules", "t1", "")
    assert not reader.contested(reader.faults[0]) and reader.contested(reader.faults[1])
    assert reader.due(both, None, free=False) is reader.faults[0]
    reader.reported(reader.faults[0], both)
    reader.outcome(True)
    # Without a free report left the contested name is skipped; with one it is reported.
    assert reader.due(alone, both, free=False) is None and reader.faults[1]["state"] == "skipped"
    reader.faults[1]["state"] = "open"
    assert reader.due(alone, both, free=True) is reader.faults[1]


def test_a_false_report_refutes_the_names_it_covered():
    reader, _ = book(load(CONFIG_PATH, {"logbook": {"enabled": False}}))
    issued = parse_utc("2027-06-01T17:15:00Z").timestamp()
    first, second = issued + 3600, issued + 7200
    reader._merge([first, second], [], [], [], issued, "model", "t1", "")
    reader.reported(reader.faults[1], second)
    reader.faults[0]["state"] = "open"
    reader.outcome(False)
    assert [item["state"] for item in reader.faults] == ["refuted", "false"]


def test_rules_read_the_log_without_a_model(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OBSERVER_MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("OBSERVER_MODEL_API_KEY", raising=False)
    events = []
    reader = Logbook(SimpleNamespace(longitude=18.4), {"site": {"utc_offset_hours": 2.0}}, load(CONFIG_PATH),
                     lambda kind, payload: events.append((kind, payload)))
    text = ("【祥子】请记录工程组通知：6/1（周二）20:30 前后动导星相机。这是硬件问题，不报修画面就会持续报废，到点请报修。\n"
            "【立希】6/2 夜里平场灯和镜盖测试 3 段：0:15到0:30、0:45到1:15、2:30到2:45。拍出来都是废片，但不是故障，请勿报修，报了算误报。")
    reader.collect([request(text, issued="2027-06-01T17:15:00Z")])
    assert reader.reader is None and reader.read["rules"]
    assert [item["start"] for item in reader.faults] == [parse_utc("2027-06-01T18:30:00Z").timestamp()]
    assert not reader.contested(reader.faults[0])
    assert (parse_utc("2027-06-02T22:15:00Z").timestamp(), parse_utc("2027-06-02T22:30:00Z").timestamp()) in reader.quiet


def test_reader_stays_off_without_credentials(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OBSERVER_MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("OBSERVER_MODEL_API_KEY", raising=False)
    reader, events = book()
    reader.collect([request(LOG)])
    assert reader.reader is None and not reader.queue and events[0][0] == "logbook_unavailable"


def test_observer_reports_the_fault_the_log_names(monkeypatch, publication):  # noqa: F811
    fake_model(monkeypatch, {"now": [{"time": "2026-10-01 20:00", "zone": "utc"}]})
    config = load(CONFIG_PATH, {"model": {"enabled": False}, "logbook": {"rules": False}})
    observer = Observer(publication, config)
    snapshot = {"now_utc": "2026-10-02T01:00:00Z", "new_messages": [request(LOG, issued="2026-10-02T00:30:00Z")],
                "wallclock": {"remaining_seconds": 900}}
    assert observer.respond(snapshot) == {"action": "report"}
    assert observer.instrument.pending["kind"] == "logbook"
    outcome = {"record_type": "report_result", "correct": True, "repaired": True, "issued_at_utc": "2026-10-02T01:00:00Z"}
    later = {"now_utc": "2026-10-02T01:00:00Z", "new_messages": [outcome], "wallclock": {"remaining_seconds": 899},
             "last_result": {"action": "report", "correct": True}}
    assert observer.respond(later)["action"] != "report"
    assert observer.logbook.faults[0]["state"] == "correct" and observer.instrument.repaired


def test_pinned_terrain_height_survives_resampling():
    model = TerrainModel(["NE"], {"terrain_particles": 200})
    assert model.pin("NE", 33.0) and not model.pin("S", 40.0)
    model.sectors[0]["log_weight"] = np.linspace(0, -50, 200)
    model._resample()
    assert np.all(model.sectors[0]["top"] == 33.0)
    assert model.status()["sectors"][0]["top"] == 33.0


@pytest.mark.parametrize("patch", [{"logbook": {"max_calls": -1}}, {"logbook": {"reports": 1}},
                                   {"logbook": {"per_decision": 0}}])
def test_logbook_configuration_is_validated(patch):
    with pytest.raises(ValueError):
        load(CONFIG_PATH, patch)


@pytest.mark.parametrize("line, decoded", [
    ("【祥子】（加密了，钥匙：29）VZ-QZ vhfwru, 1/2 2:45-4:15 orfdo (orqj-udqjh): khdylhu forxg, vr hashfw uhgxfhg txdolwb.",
     "凯撒解码：SW-NW sector, 1/2 2:45-4:15 local (long-range): heavier cloud, so expect reduced quality."),
    ("【灯】（练习摩斯……）○●●●● ●●●●● ●○○●○ ●●○○○ / ○○●●● ○●●●● ●●●●● ●●●●● ●○○○○● ●●●●● ○●●●● ○○○●● ●●●●● / ●●● ●○● / ● ○○○○ ○○ ●○",
     "摩斯解码：10/7 2100-0130 OK THIN"),
    ("【灯】……换个写法：tiTATATATA TATATATATA TAtitiTAti tiTATATATA TATATATATA / titiTATATA tititiTATA titititiTA tititititi",
     "摩斯解码：10/10 2345"),
    ("【灯】东南到南一带云比较厚，11/15（周日）（哼一句：re re : fa sol）到2:30…效果打折。", "唱名时刻：22:45"),
    ("【灯】预报说12/5（周六）♪do 高音re : 休止 休止♪到12/6（周日）0:00满天薄云", "唱名时刻：19:00"),
])
def test_encoded_log_parts_are_decoded_in_code(line, decoded):
    assert decode_log(line) == f"{line}〔{decoded}〕"


@pytest.mark.parametrize("line", ["time-critical follow-up",
                                  "【祥子】Thin cloud expected in W-NW, 12/6 23:30-12/7 19:30 local (long-range); we can still observe through it.",
                                  "【立希】今晚南边到西南风大，整晚关，别往那指。"])
def test_plain_log_lines_stay_unchanged(line):
    assert decode_log(line) == line
