"""Model reading of the duty log that some cards publish in observation-request texts.

Cards A1-D1 (version 5) issue dozens of zero-reward observation requests whose reason is a
handover log of the night staff: code words for weather, Caesar-shifted English, times
hummed as solfege, corrections ("old chart, use this one"). The log says when a drop in
throughput is the instrument and not the sky ("file a ticket now") and how high the
announced terrain really is. A correct report repairs the instrument, while the
statistical monitor needs nights of evidence and holds back after its first repair, so a
fault the log names is reported at once. Ordinary cards publish no such text and the
reader never acts on them.
"""

from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timedelta, timezone

from .geometry import parse_utc
from .services import load_client

SYSTEM_PROMPT = (
    "你是天文台巡天程序的值班日志解读器。用户消息是一个 JSON：issued_at_utc 和 issued_at_local 是这条值班日志的发布时刻，"
    "site_utc_offset_hours 是站点当地时间相对 UTC 的时差，log 是日志正文（几位值班员的交接对话，可能夹杂暗号、凯撒密码、"
    "唱名报时、摩斯码、日文或繁体字；后面的更正优先于前面的说法；行末〔〕里是程序预先解出的凯撒密文、摩斯码和唱名时刻，"
    "可以直接采用）。只依据日志内容，返回一个 JSON 对象，不要输出其他文字：\n"
    '{"fault_report": true 或 false, "fault_start": "YYYY-MM-DD HH:MM" 或 null, "fault_start_zone": "local" 或 "utc", '
    '"terrain": [{"direction": "N|NE|E|SE|S|SW|W|NW", "altitude_deg": 数字}], "reason": "一句话"}\n'
    "fault_report：只有当日志说望远镜或仪器的吞吐量、效率或计数已经突然下降、至今没有恢复、不是天气或云的原因，并要求提单、"
    "报修、叫工程组或打电话时为 true。预告将来要做的工程（例如要动导星相机）、会一晚比一晚好转而不必处理的“晃动”、"
    "只是提醒留意噪声尖峰的，都为 false。\n"
    "fault_start：那次突然下降开始的日期和时刻，根据“昨晚”“今晚”“刚才”和发布时刻推断完整日期；日志按 UTC 写的时刻填 "
    "utc，否则填 local。\n"
    "terrain：日志给出的某个方向的山、稜线或地形遮挡的高度（度）；同一方向有更正或“以……为准”时只取最后确认的数值；"
    "没有就返回空列表。"
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
        for segment in re.findall(r"[A-Za-z][A-Za-z0-9 ,.;:/()'~-]{11,}", line):
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
        self.terrain: dict[str, float] = {}
        self.applied_terrain: dict[str, float] = {}
        self.readings = 0
        self.reader = None
        if not self.settings.get("enabled", False):
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
        """Queue every new long request text (the log), each distinct text once."""
        if self.reader is None:
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
            self.queue.append((issued, str(message.get("issued_at_utc")), text[:longest]))

    def process(self, sequence) -> None:
        """Read a few queued texts; a failed or exhausted reader leaves the queue unread."""
        if self.reader is None:
            return
        for _ in range(int(self.settings.get("per_decision", 2))):
            if not self.queue or not self.reader.enabled:
                return
            issued, stamp, text = self.queue.pop(0)
            local = datetime.fromtimestamp(issued, timezone.utc) + timedelta(hours=self.offset_hours)
            payload = {"issued_at_utc": stamp, "issued_at_local": local.strftime("%Y-%m-%d %H:%M %A"),
                       "site_utc_offset_hours": self.offset_hours, "log": decode_log(text)}
            reading = self.reader.extract("duty_log", SYSTEM_PROMPT, payload, sequence)
            self.readings += 1
            if isinstance(reading, dict):
                self._apply(reading, issued)

    def _moment(self, value, zone) -> float | None:
        if not isinstance(value, str):
            return None
        try:
            moment = datetime.strptime(value.strip()[:16].replace("T", " ") + " +0000", "%Y-%m-%d %H:%M %z")
        except ValueError:
            return None
        shift = 0.0 if str(zone).strip().lower() == "utc" else self.offset_hours
        return (moment - timedelta(hours=shift)).timestamp()

    def _apply(self, reading: dict, issued: float) -> None:
        if reading.get("fault_report") is True and self.settings.get("reports", True):
            start = self._moment(reading.get("fault_start"), reading.get("fault_start_zone"))
            if start is not None and start > issued + 3600.0:
                start -= 86400.0  # "last night" dated to the day of the log
            if start is None or start > issued + 3600.0 or start < issued - 10 * 86400.0:
                # An unusable time still names a fault that has already happened.
                start = issued - 12 * 3600.0
            merge = float(self.settings.get("merge_hours", 3.0)) * 3600.0
            if not any(abs(item["start"] - start) <= merge for item in self.faults):
                self.faults.append({"start": start, "issued": issued, "state": "open"})
                self.emit("logbook_fault", {"start": round(start), "issued": round(issued),
                                            "reason": str(reading.get("reason", ""))[:160]})
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

    def due(self, now: float, last_correct: float | None) -> dict | None:
        """The oldest named fault that no correct report has repaired since it began."""
        for item in self.faults:
            if item["state"] != "open":
                continue
            if last_correct is not None and last_correct >= item["start"] - 3600.0:
                item["state"] = "covered"
                continue
            if item["start"] <= now:
                return item
        return None

    def reported(self, item: dict, now: float) -> None:
        item["state"], item["reported"] = "reported", now

    def outcome(self, correct: bool) -> None:
        for item in self.faults:
            if item["state"] == "reported":
                item["state"] = "correct" if correct else "false"

    def status(self) -> dict:
        return {"enabled": self.reader is not None, "readings": self.readings, "queued": len(self.queue),
                "faults": [{"start": round(item["start"]), "state": item["state"]} for item in self.faults],
                "terrain": dict(self.terrain),
                "reader": self.reader.status() if self.reader is not None else None}
