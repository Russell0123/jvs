from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from reminderbot.router import route
from reminderbot.settings import DEFAULTS

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 10, 29, 14, 0, tzinfo=TZ)


def r(text):
    return route(text, NOW, DEFAULTS)


@pytest.mark.parametrize("text,body", [
    ("明天下午三點提醒我交報告", "交報告"),
    ("提醒我明天下午三點交報告", "交報告"),
    ("交報告 明天提醒我", "交報告"),
    ("提醒我明天要去看牙醫", "去看牙醫"),
    ("10分鐘後叫我關火", "關火"),
    ("記得明天帶傘", "帶傘"),
    ("每天早上8點提醒我吃藥喔", "吃藥"),
    ("下週三提醒  交作業", "交作業"),
    ("明天提醒我，三點開會", "開會"),
    ("明天提醒我看天氣", "看天氣"),
])
def test_reminder(text, body):
    it = r(text)
    assert it.kind == "reminder", it
    assert it.text == body
    assert it.when is not None


def test_reminder_needs_time():
    it = r("提醒我買牛奶")
    assert it.kind == "reminder" and it.error


@pytest.mark.parametrize("text,cat,body", [
    ("記住 靈感：做一個會唱歌的貓", "靈感", "做一個會唱歌的貓"),
    ("我想看 葬送的芙莉蓮", "想看", "葬送的芙莉蓮"),
    ("靈感 用貓當主角的漫畫", "靈感", "用貓當主角的漫畫"),
    ("記一下 車停在B2-37", "備忘", "車停在B2-37"),
    ("記住 主題：台灣的廟宇建築", "主題", "台灣的廟宇建築"),
])
def test_note(text, cat, body):
    it = r(text)
    assert (it.kind, it.category, it.text) == ("note", cat, body)


@pytest.mark.parametrize("text,scope,cat", [
    ("我有哪些提醒", "reminders", None),
    ("列出我的提醒", "reminders", None),
    ("我想看什麼", "notes", "想看"),
    ("列出靈感", "notes", "靈感"),
    ("我的記事", "notes", None),
])
def test_list(text, scope, cat):
    it = r(text)
    assert (it.kind, it.scope, it.category) == ("list", scope, cat)


def test_cancel_and_delete():
    assert r("取消 3").ids == [3] and r("取消 3").kind == "cancel"
    assert r("取消提醒 3, 5").ids == [3, 5]
    assert r("刪除靈感 2").kind == "delete_note"


def test_timer():
    it = r("倒數 1小時30分 泡麵")
    assert (it.kind, it.seconds, it.text) == ("timer", 5400, "泡麵")
    assert r("計時").error


def test_weather():
    assert r("天氣").kind == "weather"
    assert r("台中天氣如何").county == "臺中市"
    assert r("明天會下雨嗎").kind == "weather"


def test_snooze_help_unknown():
    assert r("延後 30分鐘").seconds == 1800
    assert r("延後到明天").text == "tomorrow"
    assert r("說明").kind == "help"
    assert r("").kind == "help"
    assert r("你好").kind == "unknown"


def test_categories_from_settings():
    s = {**DEFAULTS, "note_categories": "食譜：食譜,想煮\n其他："}
    it = route("想煮 番茄炒蛋", NOW, s)
    assert (it.kind, it.category, it.text) == ("note", "食譜", "番茄炒蛋")
