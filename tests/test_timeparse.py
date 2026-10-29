from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from reminderbot.timeparse import describe_repeat, next_occurrence, parse, parse_duration

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 10, 29, 14, 0, tzinfo=TZ)  # 週四 下午兩點


def at(*a):
    return datetime(*a, tzinfo=TZ)


@pytest.mark.parametrize("text,expected", [
    ("明天下午三點", at(2026, 10, 30, 15, 0)),
    ("提醒我明天", at(2026, 10, 30, 9, 0)),
    ("10分鐘後", at(2026, 10, 29, 14, 10)),
    ("十分鐘後", at(2026, 10, 29, 14, 10)),
    ("半小時後", at(2026, 10, 29, 14, 30)),
    ("一個半小時後", at(2026, 10, 29, 15, 30)),
    ("2小時30分後", at(2026, 10, 29, 16, 30)),
    ("30秒後", at(2026, 10, 29, 14, 0, 30)),
    ("3點", at(2026, 10, 29, 15, 0)),             # 沒講上下午 → 下午
    ("1點", at(2026, 10, 30, 13, 0)),             # 下午1點過了 → 明天
    ("8點", at(2026, 10, 29, 20, 0)),             # 早上8點過了 → 今晚8點
    ("晚上8點半", at(2026, 10, 29, 20, 30)),
    ("15:30", at(2026, 10, 29, 15, 30)),
    ("１５：３０", at(2026, 10, 29, 15, 30)),       # 全形
    ("三點十五分", at(2026, 10, 29, 15, 15)),
    ("凌晨3點", at(2026, 10, 30, 3, 0)),
    ("中午", at(2026, 10, 30, 12, 0)),
    ("今晚", at(2026, 10, 29, 20, 0)),
    ("明早", at(2026, 10, 30, 9, 0)),
    ("後天中午", at(2026, 10, 31, 12, 0)),
    ("大後天", at(2026, 11, 1, 9, 0)),
    ("三天後下午兩點", at(2026, 11, 1, 14, 0)),
    ("週五", at(2026, 10, 30, 9, 0)),
    ("週三", at(2026, 11, 4, 9, 0)),              # 這週三過了 → 下週
    ("下週三", at(2026, 11, 4, 9, 0)),
    ("下禮拜一晚上", at(2026, 11, 2, 20, 0)),
    ("下下週一", at(2026, 11, 9, 9, 0)),
    ("星期天", at(2026, 11, 1, 9, 0)),
    ("這週末", at(2026, 10, 31, 9, 0)),
    ("10/30 晚上8點", at(2026, 10, 30, 20, 0)),
    ("11月3號", at(2026, 11, 3, 9, 0)),
    ("十一月三號", at(2026, 11, 3, 9, 0)),
    ("1/5", at(2027, 1, 5, 9, 0)),
    ("2027/2/1", at(2027, 2, 1, 9, 0)),
    ("5號", at(2026, 11, 5, 9, 0)),
    ("下個月5號", at(2026, 11, 5, 9, 0)),
    ("今天", at(2026, 10, 29, 15, 0)),            # 今天的預設時刻已過 → 一小時後
])
def test_when(text, expected):
    r = parse(text, NOW)
    assert r.error is None, r.error
    assert r.when == expected


@pytest.mark.parametrize("text,rule,first", [
    ("每天早上8點", "daily", at(2026, 10, 30, 8, 0)),
    ("每晚", "daily", at(2026, 10, 29, 20, 0)),
    ("每週一三五晚上9點", "weekly:0,2,4", at(2026, 10, 30, 21, 0)),
    ("每個禮拜四下午5點", "weekly:3", at(2026, 10, 29, 17, 0)),
    ("平日早上7點半", "weekly:0,1,2,3,4", at(2026, 10, 30, 7, 30)),
    ("每個週末", "weekly:5,6", at(2026, 10, 31, 9, 0)),
    ("每月5號", "monthly:5", at(2026, 11, 5, 9, 0)),
    ("每年3月14號", "yearly:3-14", at(2027, 3, 14, 9, 0)),
    ("每30分鐘", "interval:1800", at(2026, 10, 29, 14, 30)),
    ("每隔2小時", "interval:7200", at(2026, 10, 29, 16, 0)),
])
def test_repeat(text, rule, first):
    r = parse(text, NOW)
    assert r.repeat == rule
    assert r.when == first


def test_strip_leaves_content():
    s = "明天下午三點提醒我交報告"
    assert parse(s, NOW).strip(s) == "提醒我交報告"


def test_no_time():
    assert not parse("提醒我買牛奶", NOW).matched


def test_past_date_is_error():
    assert parse("今天早上8點", NOW).error


def test_next_occurrence_monthly_31():
    nxt = next_occurrence("monthly:31", at(2026, 11, 1, 0, 0), at(2026, 1, 1, 9).timetz().replace(tzinfo=None), TZ)
    assert nxt == at(2026, 11, 30, 9, 0)


def test_parse_duration():
    assert parse_duration("計時 1小時30分")[0] == 5400
    assert parse_duration("倒數十分鐘")[0] == 600
    assert parse_duration("沒有時間")[0] is None


def test_describe():
    assert describe_repeat("weekly:0,2,4") == "每週一、三、五"
    assert describe_repeat("interval:1800") == "每 30 分鐘"
