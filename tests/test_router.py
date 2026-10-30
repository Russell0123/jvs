from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from reminderbot.router import route
from reminderbot.settings import DEFAULTS

from oldlists import OLD_LISTS

S = {**DEFAULTS, **OLD_LISTS}

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 10, 29, 14, 0, tzinfo=TZ)


def r(text):
    return route(text, NOW, S)


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
    assert it.kind == "reminder" and it.ask == "none" and it.text == "買牛奶"


@pytest.mark.parametrize("text,cat,body", [
    ("記住 靈感：做一個會唱歌的貓", "靈感", "做一個會唱歌的貓"),
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
    ("列出靈感", "notes", "靈感"),
    ("列出想畫", "notes", "想畫"),
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
    assert r("延後到明天").when.day == 30
    assert r("說明").kind == "help"
    assert r("").kind == "help"
    assert r("你好").kind == "unknown"


def test_wish_lists_from_settings():
    s = {**DEFAULTS, "wish_lists": "想煮：煮,做菜"}
    it = route("我之後想煮番茄炒蛋", NOW, s)
    assert it.kind == "wish" and [(w.category, w.text) for w in it.wishes] == [("想煮", "番茄炒蛋")]
    assert route("我今天可以煮什麼", NOW, s).category == "想煮"


@pytest.mark.parametrize("text,cat,body", [
    ("我想畫一隻穿西裝的柴犬", "想畫", "一隻穿西裝的柴犬"),
    ("我之後想畫海邊的燈塔", "想畫", "海邊的燈塔"),
    ("我有點想畫貓", "想畫", "貓"),
    ("我想找時間畫夕陽", "想畫", "夕陽"),
    ("最近好想追孤獨搖滾", "想看", "追孤獨搖滾"),
    ("我想看 葬送的芙莉蓮", "想看", "葬送的芙莉蓮"),
    ("記住我想買一台機械鍵盤", "想買", "一台機械鍵盤"),
    ("我想要入手 Switch 2", "想買", "入手 Switch 2"),
    ("我最近想學投資", "想學", "投資"),
    ("我想規劃一次日本旅行", "想做", "規劃一次日本旅行"),
])
def test_wish(text, cat, body):
    it = r(text)
    assert it.kind == "wish" and (it.wishes[0].category, it.wishes[0].text) == (cat, body)


@pytest.mark.parametrize("text", ["我不想畫柴犬", "我想找時間畫...", "想看電影", "我想看看"])
def test_not_wish(text):
    assert r(text).kind != "wish"


@pytest.mark.parametrize("text,cat", [
    ("我最近可以畫什麼", "想畫"), ("我不知道畫什麼", "想畫"), ("畫什麼好", "想畫"),
    ("有推薦我下班後可以看的影片嗎", "想看"), ("我想看什麼", "想看"), ("要買什麼", "想買"),
])
def test_suggest(text, cat):
    assert (r(text).kind, r(text).category) == ("suggest", cat)
