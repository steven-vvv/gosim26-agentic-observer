"""extract() reads the same duty-log texts logbook.py's model prompt does, but with regular
expressions and date arithmetic. Every case below uses a real sentence copied verbatim from the
A1-D1 card logs (v4/results/platform/2026-10-06/attempt-03-a1d1), so each test both documents a
writing pattern and guards the exact wording that produced it.
"""

from __future__ import annotations

import pytest

from v4.agent.observer4.geometry import parse_utc
from v4.agent.observer4.logrules import extract


def _ts(text: str) -> float:
    return parse_utc(text).timestamp()


def test_direct_notice_from_engineering_group():
    text = "【立希】工程组通知：10/1（周四）21:30 前后会动导星相机。(╬ Ò﹏Ó)动完数据全废，直到有人报修为止，到点直接报修。"
    result = extract(text, _ts("2026-09-25T12:00:00Z"), -4.0)
    assert result["appointments"] == [_ts("2026-10-02T01:30:00Z")]
    assert result["cancelled"] == [] and result["now"] == []


@pytest.mark.parametrize("line, expected", [
    ("【祥子】再念一遍：10月5日一点整动导星相机。", "2026-10-05T05:00:00Z"),
    ("【祥子】再念一遍：10月10日二点半动导星相机。(￣▽￣)", "2026-10-10T06:30:00Z"),
])
def test_restated_in_chinese_numeral_clock_does_not_crash(line, expected):
    # Regression: "M月D日" used to become "M/D" with no separator, so an immediately following
    # spoken clock (e.g. "一点整" -> "1:00") fused onto it ("10/5" + "1:00" -> "10/51:00") and the
    # date regex swallowed the clock's first digit, raising ValueError while building datetime().
    # _normalize() now leaves a trailing space after the date substitution to prevent the fusion.
    result = extract(line, _ts("2026-09-30T12:00:00Z"), -4.0)
    assert result["appointments"] == [_ts(expected)]


def test_utc_restatement_dedupes_with_the_local_announcement():
    text = (
        "【立希】工程组通知：导星相机 10/20（周二）21:45前后动。动完数据全废，没人报修就一直废着，所以到点直接报修。\n"
        "【祥子】同一件事按 UTC 写就是：10/21（UTC）1:45 动导星相机。"
    )
    result = extract(text, _ts("2026-10-19T23:38:00Z"), -4.0)
    # Local 10/20 21:45 at UTC-4 names the same instant as 10/21 01:45 UTC: one appointment.
    assert result["appointments"] == [_ts("2026-10-21T01:45:00Z")]


@pytest.mark.parametrize("line, issued, expected", [
    (("【立希】ガイダーカメラの作業は11/1の1時45分ごろだ。そこからは報告するまでデータが全部だめになる。"
      "( ´ー`)その時刻に故障報告を出してくれ。"), "2026-10-25T12:00:00Z", "2026-11-01T05:45:00Z"),
    (("【立希】ガイダーカメラの作業は11/24の23時30分ごろ。そこからは報告するまでデータが全部使えなくなる。"
      "だからその時刻に故障報告を出せ。(-_-#)"), "2026-11-20T12:00:00Z", "2026-11-25T03:30:00Z"),
    (("【祥子】ガイダーカメラの作業は12/27の1時15分前後です。その後は故障報告が出るまで全データが無効になるため、"
      "その時刻ちょうどに報告してください。"), "2026-12-20T12:00:00Z", "2026-12-27T05:15:00Z"),
    (("【祥子】ガイダーカメラの作業は1/14の1時00分頃です。以降は報告があるまでデータがすべてだめなので、"
      "その時刻に故障報告をお願いします。(￣▽￣)"), "2027-01-10T12:00:00Z", "2027-01-14T05:00:00Z"),
])
def test_japanese_date_and_clock(line, issued, expected):
    result = extract(line, _ts(issued), -4.0)
    assert result["appointments"] == [_ts(expected)]


@pytest.mark.parametrize("line, issued, expected", [
    ("【立希】灯记错了，10/1 19:30 不对，导星相机是 10/1 21:30。以工程组通知为准。",
     "2026-09-28T12:00:00Z", "2026-10-02T01:30:00Z"),
    ("【祥子】灯记错了：10/18 3:00 不对，导星相机是 10/18 1:30，请以工程组通知为准。",
     "2026-10-15T12:00:00Z", "2026-10-18T05:30:00Z"),
    ("【祥子】灯记错了：10/23 0:15 不对，导星相机是 10/22 22:45，请以工程组通知为准。",
     "2026-10-20T12:00:00Z", "2026-10-23T02:45:00Z"),
    ("【祥子】灯记错了：10/26 21:30 不对，(^_^)导星相机是 10/26 23:30，以工程组通知为准。",
     "2026-10-20T12:00:00Z", "2026-10-27T03:30:00Z"),
])
def test_correction_keeps_the_corrected_time_not_the_wrong_one(line, issued, expected):
    # "灯记错了 X 不对，导星相机是 Y": only Y (the last date/time pair) is reported.
    result = extract(line, _ts(issued), -4.0)
    assert result["appointments"] == [_ts(expected)]


def test_cancellation_produces_no_appointment():
    text = "【立希】刚接到工程组的电话，10/20 1:30 那次导星相机取消了，不动，别報。( ´ー`)"
    result = extract(text, _ts("2026-10-19T12:00:00Z"), -10.0)
    assert result["appointments"] == []
    assert result["cancelled"] == [_ts("2026-10-20T11:30:00Z")]


def test_postponement_by_a_chinese_numeral_duration():
    text = ("【祥子】导星相机原定于 10/15 23:45，工程组刚通知推迟一个半小时。"
            "请按推迟后的时间报修，原定那个时间点不必报。(￣▽￣)")
    result = extract(text, _ts("2026-10-14T10:00:00Z"), -4.0)
    # The original time is cancelled; the postponed time (1.5h later) becomes the appointment.
    assert result["cancelled"] == [_ts("2026-10-16T03:45:00Z")]
    assert result["appointments"] == [_ts("2026-10-16T05:15:00Z")]


def test_rain_deferral_conditional_then_confirmed():
    text = (
        "【立希】导星相机排在 11/18 23:30；11/18 那晚要是下雨，就顺延一天，同一时间。\n"
        "【祥子】再补一句：１１／１８ 那晚下雨了，因此顺延一天。(￣▽￣)"
    )
    result = extract(text, _ts("2026-11-17T12:00:00Z"), -4.0)
    # The conditional appointment is withdrawn once rain is confirmed; only the +1 day slot stands.
    assert result["cancelled"] == [_ts("2026-11-19T03:30:00Z")]
    assert result["appointments"] == [_ts("2026-11-20T03:30:00Z")]


def test_multiday_shutdown_quiet_window():
    text = ("【祥子】灯之前讲的要改一下：(￣▽￣)主镜重新镀膜是从 1/25（周一）晚上就开始停机的，"
            "并不是第二天，2/6 早上恢复。以此为准。")
    result = extract(text, _ts("2027-01-20T12:00:00Z"), -9.0)
    # Evening (18:00) of the start date to morning (08:00) of the end date, spanning many nights.
    assert result["quiet"] == [[_ts("2027-01-26T03:00:00Z"), _ts("2027-02-06T17:00:00Z")]]
    assert result["appointments"] == []


def test_single_night_multi_segment_quiet_window():
    text = ("【祥子】10/1 当晚有平场灯与镜盖测试，共 3 段：23:45到0:00、1:30到1:45、2:45到3:00。"
            "這些都是废片，但不属于故障，请勿報修，報修按误報处理。（车停在 B2 区 17 号）")
    result = extract(text, _ts("2026-09-28T12:00:00Z"), -4.0)
    assert result["quiet"] == [
        [_ts("2026-10-02T03:45:00Z"), _ts("2026-10-02T04:00:00Z")],
        [_ts("2026-10-02T05:30:00Z"), _ts("2026-10-02T05:45:00Z")],
        [_ts("2026-10-02T06:45:00Z"), _ts("2026-10-02T07:00:00Z")],
    ]
    assert result["appointments"] == []


def test_now_crash_throughput_not_recovered():
    text = ("【立希】3点10分起，吞吐量一下掉到原来的5成左右，到现在没回来，刚才的事。"
            "allsky 一片黑，(ﾟДﾟ;)星点不胖。不是天的事，打那个电话吧。")
    result = extract(text, _ts("2026-10-15T14:00:00Z"), -4.0)
    assert result["now"] == [_ts("2026-10-15T07:10:00Z")]
    assert result["appointments"] == []


def test_vibration_cannot_explain_the_drop():
    text = ("【立希】前一半是。再单独往下跳一截是在大概2点40分，(-_-#)换导星没用，"
            "晃动解释不了这一下。叫工程组。（有事打分机 214）")
    result = extract(text, _ts("2026-11-03T14:00:00Z"), -4.0)
    assert result["now"] == [_ts("2026-11-03T06:40:00Z")]


def test_test_window_relative_survives_the_segment_listing_written_after_it():
    # In the real card, the "after the first flat-lamp segment ends" sentence is written BEFORE
    # the segment-listing line that gives that end time (plus its Caesar-encoded duplicate in
    # between). A single top-to-bottom pass would reach the relative sentence before
    # quiet_first_end[(10, 17)] exists; extract() runs quiet windows as a first pass over the
    # whole text precisely so this real ordering still resolves.
    text = (
        "【祥子】10/17 那晚，第一段平场灯测试完毕以後，( ´∀｀)間隔两个半小時，工程组着手检修导星相机。"
        "報修应在那一時刻提出，而非测试过程中。\n"
        "【祥子】（シーザー暗号、( ´∀｀)ずらし数は 12）Iadw az ftq sgupqd omyqdm nqsuze fia mzp m tmxr "
        "tagde mrfqd fqef iuzpai za. 1 qzpe, az ftq 10/17 zustf. Bxqmeq dmueq uf mf ftmf yayqzf "
        "dmftqd ftmz pgduzs ftq fqef.\n"
        "【立希】10/17 晚上有平场灯和镜盖测试，共 3 段：21:45–22:15、22:45–23:15、(-_-#)1:30–1:45。"
        "画面全是废片，但不算故障，报修就是误报。"
    )
    result = extract(text, _ts("2027-10-13T03:49:00Z"), -9.0)
    # First segment ends 22:15 local on 10/17; +2.5h -> 00:45 local on 10/18 -> 09:45 UTC at -9.
    # The Caesar duplicate restates the same instant and must dedupe, not double it.
    assert result["appointments"] == [_ts("2027-10-18T09:45:00Z")]


def test_relative_to_previous_appointment_with_a_signed_delta():
    text = (
        "【祥子】工程组通知，请记录：6/11（周五）3:15 前后会动导星相机。"
        "这属于硬件问题，不报修的话拍出来一直是废片，请在那个时间点报修。\n"
        "【祥子】工程组通知：6/12 那次导星相机动作，比上面 6/11 那次早15 分钟（按钟点算）。请到点报修。"
    )
    result = extract(text, _ts("2027-06-10T15:51:00Z"), -9.0)
    assert result["appointments"] == [_ts("2027-06-11T12:15:00Z"), _ts("2027-06-12T12:00:00Z")]


def test_relative_to_previous_appointment_at_the_same_time():
    text = (
        "【祥子】导星相机排在 12/9 1:00；倘若 12/9 当晚下雨，就顺延一天，时间不变。(^_^)\n"
        "【祥子】补充一点：12/9 当晚下雨了，所以顺延一天。\n"
        "【立希】工程组通知：12/11 夜里导星相机还要再动一次，时间与上面 12/10 那次相同（同一时间）。"
        "到点报修。（第 3 杯咖啡了）"
    )
    result = extract(text, _ts("2026-12-06T04:28:00Z"), 2.0)
    assert result["cancelled"] == [_ts("2026-12-08T23:00:00Z")]
    # 12/9 1:00 rained out to 12/10 1:00; 12/11 is announced as the same clock time as that move.
    assert result["appointments"] == [_ts("2026-12-09T23:00:00Z"), _ts("2026-12-10T23:00:00Z")]


def test_extract_never_raises_on_malformed_or_unanticipated_lines():
    text = (
        "【立希】工程组通知：10/1（周四）21:30 前后会动导星相机。(╬ Ò﹏Ó)动完数据全废，到点直接报修。\n"
        "【乱写】导星相机要在 2/30 10:00 动一下，数据全废。\n"  # no such day in any year
        "【乱写】导星相机要在 3/5 25:30 动一下，数据全废。\n"  # no such hour
        "完全不成句的噪声内容\n"
    )
    result = extract(text, _ts("2026-09-25T12:00:00Z"), -4.0)
    # The two impossible lines are skipped; the one well-formed line is still reported.
    assert result["appointments"] == [_ts("2026-10-02T01:30:00Z")]
    assert extract("", _ts("2026-09-25T12:00:00Z"), -4.0) == {
        "appointments": [], "cancelled": [], "quiet": [], "now": [],
    }
