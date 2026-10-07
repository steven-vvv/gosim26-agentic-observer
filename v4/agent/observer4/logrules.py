"""Deterministic (non-model) reader for the duty-log texts described in logbook.py.

extract() mirrors what logbook.py's model prompt asks a language model to do, but reads
decode_log(text) with regular expressions and date arithmetic only: no model calls. It
resolves local/UTC/foreign clocks, Caesar/Morse/solfege encodings (already decoded by
decode_log), corrections, rumours, cancellations, postponements, rain deferrals, flat-lamp
"that night" segments, and the two relative-time phrasings (offset from a flat-lamp test
window, offset from an earlier appointment's clock reading).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .logbook import decode_log

_INVISIBLE = str.maketrans("", "", "\u200b‌‍﻿")
_FULLWIDTH = str.maketrans(
    "０１２３４５６７８９／：．－−〜～–—＋",
    "0123456789/:.------+",
)

_CN_DIGIT = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_HOUR_WORD = r"(?:二十[一二三]?|十[一二三四五六七八九]?|[零一二三四五六七八九])"
_CLOCK_RE = re.compile(
    rf"(?P<h>\d{{1,2}}|{_HOUR_WORD})点(?P<m>整|半|[一二三]刻|\d{{1,2}}分|[一二三四五六七八九十]{{1,3}}分)?"
)
_SOLFEGE_TIME_RE = re.compile(r"唱名时刻[：:]\s*(\d{1,2}):(\d{2})")
_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})")
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")

GUIDER_RE = re.compile(r"导星相机|ガイダーカメラ|guider camera", re.IGNORECASE)
QUIET_RE = re.compile(
    r"平场灯|镜盖|フラット|flat-lamp|flat lamp|停机|停止|停電|检修|検修|鍍膜|镀膜|コーティング|油圧|液压|"
    r"换油|変圧器|变压器|ドームモーター|全面停止",
    re.IGNORECASE,
)
QUIET_RUMOR_RE = re.compile(r"有人说|有人說|没见|沒見|没看到|没看见|没有正式通知|听说|聽說|先别当真|先别信|うわさ|未確認")
RUMOR_RE = re.compile(r"听说|聽說|有人说|有人說|有传言|有傳言|传言|傳言|据说|沒确认|没确认|没確認|未確認|うわさ|未确认")
CANCEL_RE = re.compile(r"取消")
POSTPONE_RE = re.compile(r"推迟|推遲|postponed", re.IGNORECASE)
CORRECTION_RE = re.compile(r"记错|記錯|搞错|搞錯|更正|纠正|糾正|弄错|弄錯|记岔|記岔|记混|記混|搞混|弄混|搞岔|有误|有誤|不对|不對|错了|錯了")
RELATIVE_PREV_DELTA_RE = re.compile(r"比.{0,14}(?:前面|上面|上述).{0,14}(早|晚).{0,14}(?:分钟?|小时)")
RELATIVE_PREV_SAME_RE = re.compile(r"(?:与|與).{0,14}(?:前面|上面|上述).{0,14}相同")
RELATIVE_PREV_RE = re.compile(rf"(?:{RELATIVE_PREV_DELTA_RE.pattern})|(?:{RELATIVE_PREV_SAME_RE.pattern})")
TESTWINDOW_RE = re.compile(r"test window no\.?\s*1|号试验窗|第一段.{0,12}(?:平场灯)?测试(?:结束|完毕)")
RAIN_CONDITIONAL_RE = re.compile(
    r"(?:排在|定于|定在|安排在)\s*(?P<mo>\d{1,2})/(?P<da>\d{1,2})\s*(?P<h>\d{1,2}):(?P<mi>\d{2})"
    r".{0,40}下雨.{0,12}顺延一天"
)
RAIN_CONFIRM_RE = re.compile(
    r"(?P<mo>\d{1,2})/(?P<da>\d{1,2})\s*(?:那晚|当晚|這一晚|这一晚)?.{0,10}下雨了.{0,16}顺延一天"
)
NOW_CRASH_RE = re.compile(r"吞吐量.{0,80}(?:没|沒|未|还没|还沒|仍未|至今未)(?:恢复|回来|回來)")
NOW_VIBRATION_RE = re.compile(r"晃动解释不了.{0,20}叫工程组|叫工程组.{0,20}晃动解释不了|又.{0,6}跳.{0,10}晃动解释不了")
YESTERDAY_RE = re.compile(r"昨晚|昨夜|昨日")

_DELTA_PATTERNS = [
    (re.compile(r"an hour and a half", re.IGNORECASE), 90.0),
    (re.compile(r"two hours and (?:45|forty-five) minutes", re.IGNORECASE), 165.0),
    (re.compile(r"two and a half hours", re.IGNORECASE), 150.0),
    (re.compile(r"half an hour|half a hour", re.IGNORECASE), 30.0),
    (re.compile(r"an hour and 15 minutes|an hour and a quarter", re.IGNORECASE), 75.0),
    (re.compile(r"two hours", re.IGNORECASE), 120.0),
    (re.compile(r"three hours", re.IGNORECASE), 180.0),
    (re.compile(r"an hour\b|one hour", re.IGNORECASE), 60.0),
]

# A CN numeral 0-99: tens digit + 十 + ones (四十五), 十 + ones (十五), a bare digit/两, or ASCII digits.
_CN_NUM_TOKEN = r"(?:[一二三四五六七八九]十[一二三四五六七八九]?|十[一二三四五六七八九]?|[零一二三四五六七八九两]|\d{1,2})"
_HOUR_HALF_RE = re.compile(rf"(?P<h>{_CN_NUM_TOKEN})个半小时")
_HOUR_MINUTE_RE = re.compile(rf"(?P<h>{_CN_NUM_TOKEN})(?:个)?小时(?P<m>{_CN_NUM_TOKEN})分钟?")
_HOUR_ONLY_RE = re.compile(rf"(?P<h>{_CN_NUM_TOKEN})(?:个)?小时")
_HALF_HOUR_RE = re.compile(r"半(?:个)?小时")
_MINUTE_ONLY_RE = re.compile(rf"(?P<m>{_CN_NUM_TOKEN})分钟")


def _cn_num(token: str) -> int:
    """A Chinese numeral up to 99, e.g. '二十三' -> 23, '十五' -> 15, '两' -> 2."""
    if token.isdigit():
        return int(token)
    if "十" in token:
        left, _, right = token.partition("十")
        tens = _CN_DIGIT.get(left, 1) if left else 1
        ones = _CN_DIGIT.get(right, 0) if right else 0
        return tens * 10 + ones
    return _CN_DIGIT.get(token, 0)


def _clock_sub(match: re.Match) -> str:
    hour = _cn_num(match.group("h"))
    tail = match.group("m")
    if tail is None or tail == "整":
        minute = 0
    elif tail == "半":
        minute = 30
    elif tail.endswith("刻"):
        minute = {"一": 15, "二": 30, "三": 45}[tail[0]]
    elif tail.endswith("分"):
        minute = _cn_num(tail[:-1])
    else:
        minute = 0
    start = match.start()
    if hour == 12 and "半夜" in match.string[max(0, start - 6):start]:
        hour = 0
    return f"{hour}:{minute:02d}"


def _normalize(line: str) -> str:
    """Fold full-width digits/punctuation, invisible separators and spoken clocks to H:MM."""
    line = line.translate(_INVISIBLE).translate(_FULLWIDTH)
    # A trailing space keeps a following clock reading (e.g. "一点整" -> "1:00") from fusing
    # onto the date, which would otherwise let the date regex swallow the clock's first digit.
    line = re.sub(r"(\d{1,2})月(\d{1,2})日", r"\1/\2 ", line)
    line = re.sub(r"(\d{1,2})[時时](\d{1,2})分", lambda m: f"{m.group(1)}:{int(m.group(2)):02d}", line)
    line = line.replace("小時", "小时")
    line = _CLOCK_RE.sub(_clock_sub, line)
    return line


def _delta_minutes(text: str) -> float | None:
    match = _HOUR_HALF_RE.search(text)
    if match:
        return _cn_num(match.group("h")) * 60.0 + 30.0
    match = _HOUR_MINUTE_RE.search(text)
    if match:
        return _cn_num(match.group("h")) * 60.0 + _cn_num(match.group("m"))
    match = _HALF_HOUR_RE.search(text)
    if match:
        return 30.0
    match = _HOUR_ONLY_RE.search(text)
    if match:
        return _cn_num(match.group("h")) * 60.0
    match = _MINUTE_ONLY_RE.search(text)
    if match:
        return float(_cn_num(match.group("m")))
    for pattern, value in _DELTA_PATTERNS:
        if pattern.search(text):
            return value
    match = re.search(r"(\d+)\s*(?:分钟?|minutes?)", text, re.IGNORECASE)
    if match:
        return float(match.group(1))
    match = re.search(r"(\d+)\s*(?:小时|hours?)", text, re.IGNORECASE)
    if match:
        return float(match.group(1)) * 60.0
    return None


def _issued_local(issued_utc: float, offset_hours: float) -> datetime:
    return datetime.fromtimestamp(issued_utc, timezone.utc) + timedelta(hours=offset_hours)


def _year_for(month: int, day: int, anchor: datetime) -> int:
    best_year, best_diff = anchor.year, None
    for year in (anchor.year - 1, anchor.year, anchor.year + 1):
        try:
            candidate = datetime(year, month, day)  # noqa: DTZ001 - probe date only, used to compare candidate years
        except ValueError:
            continue
        diff = abs((candidate.date() - anchor.date()).days)
        if best_diff is None or diff < best_diff:
            best_year, best_diff = year, diff
    return best_year


def _to_utc(local_dt: datetime, zone_offset: float) -> float:
    return (local_dt - timedelta(hours=zone_offset)).replace(tzinfo=timezone.utc).timestamp()


def _zone_offset(line: str, site_offset: float) -> float:
    if re.search(r"东京|東京|tokyo", line, re.IGNORECASE):
        return 9.0
    match = re.search(r"UTC\s*([+-]\s*\d{1,2})", line)
    if match:
        return float(match.group(1).replace(" ", ""))
    return site_offset


def _date_time_pairs(line: str) -> list[tuple[int, int, int, int, int]]:
    """(month, day, hour, minute, date_position) for each date paired with its nearest time."""
    dates = [(m.start(), int(m.group(1)), int(m.group(2))) for m in _DATE_RE.finditer(line)]
    times = [(m.start(), int(m.group(1)), int(m.group(2))) for m in _TIME_RE.finditer(line)]
    pairs = []
    used = set()
    for dpos, month, day in dates:
        best = None
        for tpos, hour, minute in times:
            if tpos in used:
                continue
            if abs(tpos - dpos) <= 25 and (best is None or abs(tpos - dpos) < abs(best[0] - dpos)):
                best = (tpos, hour, minute)
        if best is not None:
            used.add(best[0])
            pairs.append((month, day, best[1], best[2], dpos))
    return pairs


def _night_dt(month: int, day: int, hour: int, minute: int, anchor: datetime) -> datetime:
    """The 'X night' convention: hours before noon belong to the day after the stated date."""
    year = _year_for(month, day, anchor)
    base = datetime(year, month, day) + timedelta(days=1 if hour < 12 else 0)  # noqa: DTZ001 - local wall clock; _to_utc() applies the real offset
    return base.replace(hour=hour, minute=minute)


def _clock_pairs(line: str) -> list[tuple[str, str]]:
    """Explicit 'H:MM到H:MM' / 'H:MM-H:MM' ranges, plus a solfege-sung opening segment."""
    pairs = []
    placeholder = re.search(r"(?:哼一句|按谱子)[^、，。]*?到\s*(\d{1,2}:\d{2})", line)
    solfege = _SOLFEGE_TIME_RE.search(line)
    rest = line
    if placeholder and solfege:
        pairs.append((f"{solfege.group(1)}:{solfege.group(2)}", placeholder.group(1)))
        rest = line[: placeholder.start()] + line[placeholder.end():]
    for match in re.finditer(r"(\d{1,2}:\d{2})\s*(?:到|-)\s*(\d{1,2}:\d{2})", rest):
        pairs.append((match.group(1), match.group(2)))
    return pairs


def _handle_quiet(line: str, anchor: datetime, offset_hours: float, add_quiet) -> None:
    multiday = re.search(
        r"(\d{1,2})/(\d{1,2})[^。]*?停机[^。]*?(\d{1,2})/(\d{1,2})[^。]*?早上[^。]*?恢复", line
    ) or re.search(
        r"(\d{1,2})/(\d{1,2})[^。]*?の夜から[^。]*?(\d{1,2})/(\d{1,2})[^。]*?の朝まで", line
    )
    if multiday:
        start_year = _year_for(int(multiday.group(1)), int(multiday.group(2)), anchor)
        end_year = _year_for(int(multiday.group(3)), int(multiday.group(4)), anchor)
        start = datetime(start_year, int(multiday.group(1)), int(multiday.group(2)), 18, 0)  # noqa: DTZ001 - local wall clock; _to_utc() applies the real offset
        end = datetime(end_year, int(multiday.group(3)), int(multiday.group(4)), 8, 0)  # noqa: DTZ001 - local wall clock; _to_utc() applies the real offset
        add_quiet(_to_utc(start, offset_hours), _to_utc(end, offset_hours))
        return
    date_match = _DATE_RE.search(line)
    if not date_match:
        return
    month, day = int(date_match.group(1)), int(date_match.group(2))
    for start_str, end_str in _clock_pairs(line):
        sh, sm = (int(part) for part in start_str.split(":"))
        eh, em = (int(part) for part in end_str.split(":"))
        start = _night_dt(month, day, sh, sm, anchor)
        end = _night_dt(month, day, eh, em, anchor)
        add_quiet(_to_utc(start, offset_hours), _to_utc(end, offset_hours))


def _handle_guider(
    line: str,
    anchor: datetime,
    issued_utc: float,
    offset_hours: float,
    add_appt,
    add_cancel,
    wallclock_by_date: dict,
    rain_pending: dict,
    quiet_first_end: dict,
) -> None:
    if "【灯】" in line and not re.search(r"报修|報修|故障|report", line, re.IGNORECASE):
        # The absent-minded colleague's own recollection (even "didn't I get it right?") is never a notice.
        return

    if CANCEL_RE.search(line):
        pairs = _date_time_pairs(line)
        if not pairs:
            # A cancellation that names its clock by solfege ("8/1 （按谱子：re : fa sol） 那次...取消了").
            solfege = _SOLFEGE_TIME_RE.search(line)
            cn_date = _DATE_RE.search(line)
            if solfege and cn_date:
                pairs = [(int(cn_date.group(1)), int(cn_date.group(2)),
                          int(solfege.group(1)), int(solfege.group(2)), 0)]
        if pairs:
            month, day, hour, minute, _ = pairs[0]
            ts = _to_utc(datetime(_year_for(month, day, anchor), month, day, hour, minute), offset_hours)  # noqa: DTZ001 - local wall clock; _to_utc() applies the real offset
            add_cancel(ts)
        return

    if POSTPONE_RE.search(line):
        pairs = _date_time_pairs(line)
        delta = _delta_minutes(line)
        if pairs and delta is not None:
            month, day, hour, minute, _ = pairs[0]
            original = datetime(_year_for(month, day, anchor), month, day, hour, minute)  # noqa: DTZ001 - local wall clock; _to_utc() applies the real offset
            original_ts = _to_utc(original, offset_hours)
            add_cancel(original_ts)
            add_appt(original_ts + delta * 60.0)
        return

    if CORRECTION_RE.search(line):
        pairs = _date_time_pairs(line)
        if pairs:
            month, day, hour, minute, _ = pairs[-1]
            year = _year_for(month, day, anchor)
            ts = _to_utc(datetime(year, month, day, hour, minute), offset_hours)  # noqa: DTZ001 - local wall clock; _to_utc() applies the real offset
            add_appt(ts)
            wallclock_by_date[(month, day)] = (hour, minute)
        return

    if RELATIVE_PREV_RE.search(line):
        dates = [(int(m.group(1)), int(m.group(2))) for m in _DATE_RE.finditer(line)]
        same_match = RELATIVE_PREV_SAME_RE.search(line)
        delta_match = RELATIVE_PREV_DELTA_RE.search(line)
        signed_delta = 0.0 if same_match else _delta_minutes(line)
        if len(dates) >= 2 and signed_delta is not None:
            new_date, ref_date = dates[0], dates[1]
            if ref_date in wallclock_by_date:
                ref_hour, ref_minute = wallclock_by_date[ref_date]
                if not same_match and delta_match.group(1) == "早":
                    signed_delta = -signed_delta
                total = ref_hour * 60 + ref_minute + signed_delta
                new_hour, new_minute = int(total // 60) % 24, int(total % 60)
                year = _year_for(new_date[0], new_date[1], anchor)
                ts = _to_utc(datetime(year, new_date[0], new_date[1], new_hour, new_minute), offset_hours)  # noqa: DTZ001 - local wall clock; _to_utc() applies the real offset
                add_appt(ts)
                wallclock_by_date[new_date] = (new_hour, new_minute)
        return

    if TESTWINDOW_RE.search(line):
        date_match = _DATE_RE.search(line)
        delta = _delta_minutes(line)
        if date_match and delta is not None:
            key = (int(date_match.group(1)), int(date_match.group(2)))
            if key in quiet_first_end:
                new_dt = quiet_first_end[key] + timedelta(minutes=delta)
                add_appt(_to_utc(new_dt, offset_hours))
                wallclock_by_date[key] = (new_dt.hour, new_dt.minute)
        return

    rain = RAIN_CONDITIONAL_RE.search(line)
    if rain is None and "下雨" in line and "顺延" in line:
        # The same sentence with the clock sung as solfege ("排在 8/18 ♪re do : mi 休止♪；若 8/18 当晚下雨，则顺延一天").
        sung = _SOLFEGE_TIME_RE.search(line)
        planned = re.search(r"(?:排在|定于|定在|安排在|預定于|预定于)\s*(\d{1,2})/(\d{1,2})", line)
        if sung and planned:
            rain = re.match(r"(?P<mo>\d+)/(?P<da>\d+) (?P<h>\d+):(?P<mi>\d+)",
                            f"{planned.group(1)}/{planned.group(2)} {sung.group(1)}:{sung.group(2)}")
    if rain is not None:
        match = rain
        month, day = int(match.group("mo")), int(match.group("da"))
        hour, minute = int(match.group("h")), int(match.group("mi"))
        ts = _to_utc(datetime(_year_for(month, day, anchor), month, day, hour, minute), offset_hours)  # noqa: DTZ001 - local wall clock; _to_utc() applies the real offset
        add_appt(ts)
        rain_pending[(month, day)] = ts
        wallclock_by_date[(month, day)] = (hour, minute)
        return

    if RUMOR_RE.search(line):
        return

    if "【灯】" in line and not re.search(r"报修|報修|故障|report", line, re.IGNORECASE):
        return

    pairs = _date_time_pairs(line)
    if not pairs:
        solfege = _SOLFEGE_TIME_RE.search(line)
        cn_date = _DATE_RE.search(line)
        if not (solfege and cn_date):
            return
        month, day = int(cn_date.group(1)), int(cn_date.group(2))
        hour, minute = int(solfege.group(1)), int(solfege.group(2))
    else:
        month, day, hour, minute, _ = pairs[0]

    zone = _zone_offset(line, offset_hours)
    if re.search(rf"{month}/{day}\s*[(（]UTC[)）]", line):
        zone = 0.0
    year = _year_for(month, day, anchor)
    ts = _to_utc(datetime(year, month, day, hour, minute), zone)  # noqa: DTZ001 - local wall clock; _to_utc() applies the real offset
    add_appt(ts)
    wallclock_by_date[(month, day)] = (hour, minute)


def _resolve_now(hour: int, minute: int, line: str, anchor: datetime, offset_hours: float) -> float:
    candidate_date = anchor.date() - timedelta(days=1) if YESTERDAY_RE.search(line) else anchor.date()
    candidate = datetime.combine(candidate_date, datetime.min.time(), tzinfo=timezone.utc).replace(
        hour=hour, minute=minute
    )
    if candidate > anchor:
        candidate -= timedelta(days=1)
    return _to_utc(candidate, offset_hours)


def extract(text: str, issued_utc: float, offset_hours: float) -> dict:
    """Appointments, cancellations, quiet windows and already-happened drops from a duty log.

    Returns {"appointments": [utc_ts, ...], "cancelled": [utc_ts, ...],
    "quiet": [[start_ts, end_ts], ...], "now": [utc_ts, ...]}.
    """
    appointments: list[float] = []
    cancelled: list[float] = []
    quiet: list[list[float]] = []
    now: list[float] = []

    def add_appt(ts: float) -> None:
        if not any(abs(ts - existing) <= 60.0 for existing in appointments):
            appointments.append(ts)

    def add_cancel(ts: float) -> None:
        if not any(abs(ts - existing) <= 60.0 for existing in cancelled):
            cancelled.append(ts)

    def add_quiet(start: float, end: float) -> None:
        if not any(abs(start - s) <= 60.0 and abs(end - e) <= 60.0 for s, e in quiet):
            quiet.append([start, end])

    def add_now(ts: float) -> None:
        if not any(abs(ts - existing) <= 60.0 for existing in now):
            now.append(ts)

    # A single malformed line (an unanticipated writing pattern, a mis-paired date/time) must
    # never lose the rest of the log: each line gets its own fallback, and a failure outside the
    # per-line loop still returns whatever was already parsed.
    try:
        decoded = decode_log(text)
        anchor = _issued_local(issued_utc, offset_hours)
        wallclock_by_date: dict[tuple[int, int], tuple[int, int]] = {}
        rain_pending: dict[tuple[int, int], float] = {}
        quiet_first_end: dict[tuple[int, int], datetime] = {}
        lines = decoded.split("\n")

        # Pass 1: quiet windows first. A "that night, after the first flat-lamp segment ends"
        # appointment (pattern 10) can be written before the segment-listing line that gives
        # its end time, so quiet_first_end must be fully built before pass 2 reads it.
        for raw_line in lines:
            try:
                line = _normalize(raw_line)
            except Exception:  # noqa: BLE001, S112 - unanticipated line; skip it, keep parsing the rest
                continue
            try:
                if QUIET_RE.search(line) and not QUIET_RUMOR_RE.search(line):
                    before = len(quiet)
                    _handle_quiet(line, anchor, offset_hours, add_quiet)
                    if len(quiet) > before:
                        date_match = _DATE_RE.search(line)
                        if date_match and ("平场灯" in line or "镜盖" in line or "フラット" in line or
                                           re.search(r"flat-lamp|flat lamp", line, re.IGNORECASE)):
                            key = (int(date_match.group(1)), int(date_match.group(2)))
                            first = quiet[before]
                            first_end = datetime.fromtimestamp(first[1], timezone.utc) + timedelta(hours=offset_hours)
                            quiet_first_end.setdefault(key, first_end)
            except Exception:  # noqa: BLE001, S110 - quiet-window parsing failed for this line only
                pass

        # Pass 2: rain deferrals, already-happened drops, cancellations/postponements/
        # corrections and appointments, in the original line order.
        for raw_line in lines:
            try:
                line = _normalize(raw_line)
            except Exception:  # noqa: BLE001, S112 - unanticipated line; skip it, keep parsing the rest
                continue

            try:
                rain_confirm = RAIN_CONFIRM_RE.search(line)
                if rain_confirm:
                    key = (int(rain_confirm.group("mo")), int(rain_confirm.group("da")))
                    if key in rain_pending:
                        original_ts = rain_pending.pop(key)
                        if original_ts in appointments:
                            appointments.remove(original_ts)
                        add_cancel(original_ts)
                        new_ts = original_ts + 86400.0
                        add_appt(new_ts)
                        new_local = datetime.fromtimestamp(new_ts, timezone.utc) + timedelta(hours=offset_hours)
                        wallclock_by_date[(new_local.month, new_local.day)] = (new_local.hour, new_local.minute)
            except Exception:  # noqa: BLE001, S110 - rain-confirm parsing failed for this line only
                pass

            try:
                if NOW_CRASH_RE.search(line) or NOW_VIBRATION_RE.search(line):
                    time_match = _TIME_RE.search(line)
                    if time_match:
                        add_now(_resolve_now(int(time_match.group(1)), int(time_match.group(2)),
                                             line, anchor, offset_hours))
            except Exception:  # noqa: BLE001, S110 - now-detection parsing failed for this line only
                pass

            try:
                if GUIDER_RE.search(line) and (not QUIET_RE.search(line) or TESTWINDOW_RE.search(line)):
                    _handle_guider(line, anchor, issued_utc, offset_hours, add_appt, add_cancel,
                                   wallclock_by_date, rain_pending, quiet_first_end)
            except Exception:  # noqa: BLE001, S110 - guider parsing failed for this line only
                pass
    except Exception:  # noqa: BLE001, S110 - outer safety net, return whatever was parsed
        pass

    return {
        "appointments": sorted(appointments),
        "cancelled": sorted(cancelled),
        "quiet": sorted(quiet),
        "now": sorted(now),
    }
