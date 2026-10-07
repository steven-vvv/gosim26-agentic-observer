"""Model reading of the duty log that some cards publish in observation-request texts.

Cards A1-D1 (version 5) issue dozens of zero-reward observation requests whose reason is a
handover log of the night staff: code words for weather, Caesar-shifted English, times
hummed as solfege, corrections ("old chart, use this one"), rumours and cancellations. The
engineers announce when they will move the guide camera; from that moment the throughput
of the whole instrument stays near zero until a report repairs it, so the report is due
exactly at the announced time, once per announcement. Flat-lamp and mirror-cover tests
spoil frames too but are not faults: no report and no exposure then. The reader turns
each log into appointments, cancellations, quiet windows and drops that have already
happened; ordinary cards publish no such text and the reader never acts on them.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from datetime import datetime, timedelta, timezone

from .geometry import parse_utc
from .services import load_client

SYSTEM_PROMPT = (
    "你是天文台巡天程序的值班日志解读器。用户消息是一个 JSON：issued_at_utc 和 issued_at_local 是这条值班日志的发布时刻，"
    "site_utc_offset_hours 是站点当地时间相对 UTC 的时差，log 是日志正文（几位值班员的交接对话，可能夹杂暗号、凯撒密码、"
    "唱名报时、摩斯码、日文或繁体字；后面的更正优先于前面的说法；行末〔〕里是程序预先解出的凯撒密文、摩斯码和唱名时刻，"
    "可以直接采用）。只依据日志内容，返回一个 JSON 对象，不要输出其他文字：\n"
    '{"appointments": [{"time": "YYYY-MM-DD HH:MM", "zone": 时区}], "cancelled": [{"time": "YYYY-MM-DD HH:MM", "zone": 时区}], '
    '"quiet": [{"start": "YYYY-MM-DD HH:MM", "end": "YYYY-MM-DD HH:MM", "zone": 时区}], '
    '"now": [{"time": "YYYY-MM-DD HH:MM", "zone": 时区}], '
    '"terrain": [{"direction": "N|NE|E|SE|S|SW|W|NW", "altitude_deg": 数字}], "reason": "一句话"}\n'
    "时区写 \"local\"（站点当地时间）、\"utc\"，或日志指明的其它时钟写成 \"UTC+9\"、\"UTC-11\" 这样（例如「工程组给的是东京时间（UTC+9）」"
    "就写 UTC+9）；时刻照日志原样填，绝对不要自己换算时区，换算由程序完成。例：「工程组用的是 UTC-11 的钟：1/21 21:15 动导星相机」"
    "填 {\"time\": \"2027-01-21 21:15\", \"zone\": \"UTC-11\"}；「按钟点比前面 1/24 的那次早 15 分钟」填新日期、那次的钟点减 15 分钟、"
    "那次的时区。日期一律写完整的公历日期：日志写 M/D 就用 M/D 那一天（凌晨的时刻也就是写的那个日历日），"
    "唯独平场灯、镜盖测试这类“X 日那晚/夜里”的时段，中午以前的时刻属于第二天。\n"
    "appointments：工程组确认要动导星相机（ガイダーカメラの作業、guider camera work）或其它硬件、并说动完整机吞吐量归零或画面报废、"
    "要求到点报修的每个时刻，"
    "一条日志里有几次就列几次；同一件事的本地写法和 UTC 写法只列一次。更正（「灯记错了，X 不对，导星相机是 Y」）只取更正后的 Y；"
    "「若那晚下雨则顺延一天」后面又说「那晚下雨了，顺延一天」时，取顺延后的时刻；「推迟 N 分钟/小时」时取推迟后的时刻。"
    "以下都不要列：【灯】自己没被确认的时刻；传言（「听说也要动？没确认，先别当真」「有人说」）；平场灯、镜盖测试。\n"
    "cancelled：工程组说取消了、不动了、别报的原定时刻，以及推迟或顺延时说「原来那个点别报」的原时刻。\n"
    "quiet：拍的是废片但不许报修的时段——平场灯、镜盖测试（フラット試験、flat-lamp tests），以及停机、停止、停電、检修、"
    "换油、镀膜、点检、换导星相机停机等维护时段（多日停机写起止日期和时刻；只说早上恢复的，end 用那天 08:00）。\n"
    "now：日志说吞吐量已经突然下降、至今没有恢复、不是天气，要求提单、报修、叫工程组时，那次下降开始的时刻"
    "（根据“昨晚”“刚才”和发布时刻推断完整日期）；“会一晚比一晚好转的晃动”、噪声尖峰提醒都不算。\n"
    "terrain：日志给出的某个方向的山、稜线或地形遮挡的高度（度）；同一方向有更正或“以……为准”时只取最后确认的数值；"
    "没有就返回空列表。没有的项都返回空列表。"
)

DIRECTIONS = {"N", "NE", "E", "SE", "S", "SW", "W", "NW"}

# The log's encodings are fixed transforms, so they are undone in code and the model reads
# plain text: Caesar-shifted English (the shift is the date or a stated key; every shift is
# tried and the most English one kept), Morse code written with one of several symbol pairs,
# and clock times hummed as solfege.
VOCABULARY = frozenset(
    ["a", "about", "across", "after", "all", "also", "and", "are", "around", "at", "base", "be", "because", "before", "between", "but", "by", "can", "clear", "closed", "closes", "cloud", "cloudy", "continues", "dome", "due", "during", "east", "entire", "expect", "expected", "fine", "for", "forecast", "from", "gusts", "has", "have", "haze", "heads", "heavier", "heavy", "high", "in", "is", "it", "its", "launch", "light", "limit", "local", "long", "low", "mph", "night", "no", "north", "not", "note", "observable", "observation", "observe", "observed", "observing", "of", "on", "outlook", "over", "per", "possible", "present", "quality", "rain", "range", "reduced", "remains", "sector", "severe", "shows", "sky", "so", "south", "still", "storms", "the", "thick", "thin", "through", "time", "to", "under", "unaffected", "until", "up", "update", "view", "we", "west", "whole", "will", "wind", "window", "with"])
MORSE = {".-": "A", "-...": "B", "-.-.": "C", "-..": "D", ".": "E", "..-.": "F", "--.": "G", "....": "H", "..": "I",
         ".---": "J", "-.-": "K", ".-..": "L", "--": "M", "-.": "N", "---": "O", ".--.": "P", "--.-": "Q", ".-.": "R",
         "...": "S", "-": "T", "..-": "U", "...-": "V", ".--": "W", "-..-": "X", "-.--": "Y", "--..": "Z",
         ".----": "1", "..---": "2", "...--": "3", "....-": "4", ".....": "5", "-....": "6", "--...": "7",
         "---..": "8", "----.": "9", "-----": "0", "-..-.": "/", "-....-": "-", "---...": ":"}
MORSE_SYMBOLS = (("ti", "TA"), ("短", "长"), ("○", "●"), ("♪", "♫"), ("嘀", "嗒"), ("口", "曰"), ("·", "−"))
SOLFEGE = {"高音do": "8", "高音re": "9", "休止": "0", "do": "1", "re": "2", "mi": "3", "fa": "4", "sol": "5",
           "la": "6", "si": "7"}
_SOLFEGE_RUN = re.compile(r"(?:♪|哼[^：:）)]{0,8}[：:]|谱子[：:])\s*((?:高音do|高音re|休止|sol|do|re|mi|fa|la|si|[:：]|\s)+)")


def _shift(text: str, key: int) -> str:
    return "".join(chr((ord(c) - 97 - key) % 26 + 97) if "a" <= c <= "z" else
                   chr((ord(c) - 65 - key) % 26 + 65) if "A" <= c <= "Z" else c for c in text)


def _english(text: str) -> float:
    words = re.findall(r"[a-z]+", text.lower())
    return sum(word in VOCABULARY for word in words) / len(words) if len(words) >= 3 else 0.0


def caesar_plain(segment: str) -> str | None:
    """The most English Caesar shift of an English-looking segment, unless it is plain already."""
    scores = [_english(_shift(segment, key)) for key in range(26)]
    best = max(range(26), key=lambda key: (scores[key], -key))
    if best == 0 or scores[best] < 0.4 or scores[best] < scores[0] + 0.25:
        return None
    return _shift(segment, best)


def morse_plain(line: str) -> str | None:
    for dot, dash in MORSE_SYMBOLS:
        token = f"(?:{re.escape(dot)}|{re.escape(dash)})+"
        runs = re.findall(rf"{token}(?:[ \t/]+{token}){{3,}}", line)
        if not runs:
            continue
        words = []
        for group in max(runs, key=len).split("/"):
            letters = [MORSE.get(code.replace(dash, "-").replace(dot, "."), "?") for code in group.split()]
            if letters:
                words.append("".join(letters))
        return " ".join(words)
    return None


def solfege_plain(line: str) -> list[str]:
    result = []
    for match in _SOLFEGE_RUN.finditer(line):
        notes = re.findall(r"高音do|高音re|休止|sol|do|re|mi|fa|la|si|[:：]", match.group(1))
        digits = "".join(":" if note in ":：" else SOLFEGE[note] for note in notes)
        if any(c.isdigit() for c in digits):
            result.append(digits)
    return result


def decode_log(text: str) -> str:
    """The log with each line's decoded Caesar, Morse and solfege parts appended in 〔〕."""
    lines = []
    for line in text.split("\n"):
        notes = []
        for segment in re.findall(r"[A-Za-z][A-Za-z0-9 ,.;:/()'~+-]{11,}", line):
            plain = caesar_plain(segment)
            if plain:
                notes.append("凯撒解码：" + plain.strip())
        morse = morse_plain(line)
        if morse:
            notes.append("摩斯解码：" + morse)
        notes.extend("唱名时刻：" + value for value in solfege_plain(line))
        lines.append(line + ("〔" + "；".join(notes) + "〕" if notes else ""))
    return "\n".join(lines)


class Logbook:
    def __init__(self, task, publication: dict, config: dict, emit):
        self.settings = dict(config.get("logbook") or {})
        self.emit = emit
        site = publication.get("site") or {}
        offset = site.get("utc_offset_hours")
        self.offset_hours = (float(offset) if isinstance(offset, (int, float)) and not isinstance(offset, bool)
                             else float(round(task.longitude / 15.0)))
        self.queue: list[tuple[float, str, str]] = []
        self.seen: set[str] = set()
        self.faults: list[dict] = []
        self.cancelled: list[float] = []
        self.quiet: list[tuple[float, float]] = []
        self.terrain: dict[str, float] = {}
        self.applied_terrain: dict[str, float] = {}
        self.readings = 0
        self.waited = 0.0
        self.read = {"model": set(), "rules": set()}
        self.reader = None
        enabled = bool(self.settings.get("enabled", False))
        self.rules = enabled and bool(self.settings.get("rules", True))
        if not enabled:
            return
        client = load_client("model_advisor")
        url = os.environ.get("OBSERVER_MODEL_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
        key = os.environ.get("OBSERVER_MODEL_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if client is None or not url or not key:
            self.emit("logbook_unavailable", {"reason": "client_missing" if client is None else "endpoint_or_key_missing"})
            return
        calls = int(self.settings.get("max_calls", 120))
        options = client.AdvisorConfig(
            enabled=True,
            model=os.environ.get("OBSERVER_MODEL_NAME") or config["model"]["model"],
            timeout=float(self.settings.get("timeout_seconds", 15.0)),
            max_total_calls=calls,
            max_stage_calls=calls,
            max_consecutive_failures=int(self.settings.get("max_consecutive_failures", 4)),
            max_tokens=int(self.settings.get("max_tokens", 600)),
            min_alternative_probability=0.0,
        )
        self.reader = client.ModelAdvisor(url, key, config=options,
                                          on_event=lambda event: self.emit("model_request", event))

    def collect(self, messages: list[dict]) -> None:
        """Read every new long request text (the log) by rule now and queue it for the model."""
        if self.reader is None and not self.rules:
            return
        shortest = int(self.settings.get("min_chars", 40))
        longest = int(self.settings.get("max_chars", 6000))
        for message in messages:
            text = message.get("reason")
            if not isinstance(text, str) or len(text) < shortest:
                continue
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if digest in self.seen:
                continue
            self.seen.add(digest)
            try:
                issued = parse_utc(str(message.get("issued_at_utc"))).timestamp()
            except (TypeError, ValueError):
                continue
            if self.rules:
                self._read_rules(text[:longest], issued, digest)
            if self.reader is not None:
                self.queue.append((issued, str(message.get("issued_at_utc")), text[:longest], digest))

    def _read_rules(self, text: str, issued: float, digest: str) -> None:
        try:
            from .logrules import extract

            reading = extract(text, issued, self.offset_hours)
        except Exception as error:  # noqa: BLE001 - the model reader still reads the text
            self.emit("logbook_rules_error", {"error_type": type(error).__name__, "detail": str(error)[:160]})
            return
        self.read["rules"].add(digest)
        self._merge(reading.get("appointments", []), reading.get("cancelled", []),
                    [tuple(window) for window in reading.get("quiet", [])], reading.get("now", []),
                    issued, "rules", digest, "")

    def process(self, sequence) -> None:
        """Read a few queued texts; a failed or exhausted reader leaves the queue unread."""
        if self.reader is None:
            return
        for _ in range(int(self.settings.get("per_decision", 2))):
            if not self.queue or not self.reader.enabled:
                return
            if self.waited > float(self.settings.get("max_wait_seconds", 900.0)):
                # Reading is not charged to the budget but counts against the run's real-time
                # cap; past this much waiting the rules read the remaining texts alone.
                self.emit("logbook_wait_cap", {"waited": round(self.waited, 1), "queued": len(self.queue)})
                self.reader = None
                return
            issued, stamp, text, digest = self.queue.pop(0)
            local = datetime.fromtimestamp(issued, timezone.utc) + timedelta(hours=self.offset_hours)
            payload = {"issued_at_utc": stamp, "issued_at_local": local.strftime("%Y-%m-%d %H:%M %A"),
                       "site_utc_offset_hours": self.offset_hours, "log": decode_log(text)}
            started = time.perf_counter()
            reading = self.reader.extract("duty_log", SYSTEM_PROMPT, payload, sequence)
            self.waited += time.perf_counter() - started
            self.readings += 1
            if isinstance(reading, dict):
                self.read["model"].add(digest)
                self._apply(reading, issued, digest)

    def _moment(self, value, zone) -> float | None:
        """UTC timestamp of a 'YYYY-MM-DD HH:MM' on the site clock, UTC or a stated 'UTC+9' clock."""
        if not isinstance(value, str):
            return None
        try:
            moment = datetime.strptime(value.strip()[:16].replace("T", " ") + " +0000", "%Y-%m-%d %H:%M %z")
        except ValueError:
            return None
        zone = str(zone).strip().upper().replace("−", "-").replace("UTC ", "UTC")
        if zone == "UTC":
            shift = 0.0
        elif zone.startswith("UTC") and re.fullmatch(r"UTC[+-]\d{1,2}(\.5)?", zone):
            shift = float(zone[3:])
        else:
            shift = self.offset_hours
        return (moment - timedelta(hours=shift)).timestamp()

    def _times(self, rows, issued: float, keys=("time",)) -> list:
        """Readable moments of a reading's list, each within the season around the log."""
        horizon = float(self.settings.get("appointment_horizon_days", 45.0)) * 86400.0
        result = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            values = [self._moment(row.get(key), row.get("zone")) for key in keys]
            if any(value is None or not issued - 10 * 86400.0 <= value <= issued + horizon for value in values):
                continue
            result.append(values[0] if len(values) == 1 else tuple(values))
        return result

    def _apply(self, reading: dict, issued: float, digest: str = "") -> None:
        self._merge(self._times(reading.get("appointments"), issued), self._times(reading.get("cancelled"), issued),
                    self._times(reading.get("quiet"), issued, ("start", "end")), self._times(reading.get("now"), issued),
                    issued, "model", digest, str(reading.get("reason", ""))[:160])
        if self.settings.get("terrain", True) and isinstance(reading.get("terrain"), list):
            for row in reading["terrain"]:
                if not isinstance(row, dict):
                    continue
                direction = str(row.get("direction", "")).strip().upper()
                try:
                    altitude = float(row.get("altitude_deg"))
                except (TypeError, ValueError):
                    continue
                if direction in DIRECTIONS and 5.0 <= altitude <= 75.0:
                    self.terrain[direction] = altitude

    def _merge(self, appointments, cancelled, quiet, now, issued: float, source: str, digest: str,
               reason: str) -> None:
        """Fold one reader's reading of one text into the shared schedule."""
        merge = float(self.settings.get("merge_hours", 3.0)) * 3600.0
        for start, end in quiet:
            if 0 < end - start <= 20 * 86400.0 and (start, end) not in self.quiet:
                self.quiet.append((start, end))
                self.emit("logbook_quiet", {"start": round(start), "end": round(end), "source": source})
        # A cancelled appointment must be dropped before a new one can merge with it.
        for cancel in cancelled:
            self.cancelled.append(cancel)
            for item in self.faults:
                if item["state"] == "open" and abs(item["start"] - cancel) <= 600.0:
                    item["state"] = "cancelled"
                    self.emit("logbook_cancel", {"start": round(item["start"]), "source": source})
        # An appointment is due exactly when the engineers move the guide camera; a drop the
        # log says has already happened is due at once. Two announcements are one fault only
        # when they name the same moment (the log restates each in UTC).
        named = [(start, True) for start in appointments] + [(start, False) for start in now]
        for start, appointment in named if self.settings.get("reports", True) else []:
            if any(abs(start - cancel) <= 600.0 for cancel in self.cancelled):
                continue
            window = 600.0 if appointment else merge
            match = next((item for item in self.faults if item["state"] != "cancelled"
                          and abs(item["start"] - start) <= window), None)
            if match is not None:
                match["sources"].add(source)
                match["texts"].add(digest)
                continue
            self.faults.append({"start": start, "issued": issued, "state": "open", "future": start > issued,
                                "sources": {source}, "texts": {digest}})
            self.faults.sort(key=lambda item: item["start"])
            self.emit("logbook_fault", {"start": round(start), "issued": round(issued), "appointment": appointment,
                                        "source": source, "reason": reason})

    def contested(self, item: dict) -> bool:
        """Only one reader names the fault although the other read a text that names it."""
        if len(item["sources"]) != 1:
            return False
        other = "rules" if "model" in item["sources"] else "model"
        return bool(item["texts"] & self.read[other])

    def in_quiet(self, start: float, end: float | None = None) -> bool:
        """True when [start, end) overlaps a window whose frames are spoilt but no fault."""
        end = start + 1.0 if end is None else end
        return any(low < end and start < high for low, high in self.quiet)

    def next_boundary(self, now: float) -> float | None:
        """The next moment the instrument changes on its own: an appointment or a quiet window."""
        moments = [item["start"] for item in self.faults if item["state"] == "open" and item["start"] > now]
        moments += [low for low, _ in self.quiet if low > now]
        return min(moments) if moments else None

    def due(self, now: float, last_correct: float | None, free: bool = True) -> dict | None:
        """The oldest named fault that no correct report has repaired since it began.

        A fault both readers name is reported in any case; one that only one of them names
        (the other read the same text) only while a false report costs nothing.
        """
        for item in self.faults:
            if item["state"] != "open":
                continue
            if last_correct is not None and item["start"] <= last_correct:
                item["state"] = "covered"
                continue
            if item["start"] > now:
                return None
            if self.contested(item) and not free:
                item["state"] = "skipped"
                continue
            return item
        return None

    def reported(self, item: dict, now: float) -> None:
        item["state"], item["reported"] = "reported", now

    def outcome(self, correct: bool) -> None:
        reported = [item["reported"] for item in self.faults if item["state"] == "reported"]
        for item in self.faults:
            if item["state"] == "reported":
                item["state"] = "correct" if correct else "false"
            elif not correct and reported and item["state"] == "open" and item["start"] <= max(reported):
                # A false report proves no unrepaired fault was active then: earlier names are wrong.
                item["state"] = "refuted"

    def status(self) -> dict:
        return {"enabled": self.reader is not None, "rules": self.rules, "readings": self.readings,
                "waited": round(self.waited, 1),
                "queued": len(self.queue), "read": {name: len(texts) for name, texts in self.read.items()},
                "faults": [{"start": round(item["start"]), "state": item["state"], "sources": sorted(item["sources"])}
                           for item in self.faults],
                "quiet": len(self.quiet), "cancelled": len(self.cancelled),
                "terrain": dict(self.terrain),
                "reader": self.reader.status() if self.reader is not None else None}
