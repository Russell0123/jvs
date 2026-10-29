"""把一句話分類成要做的事（關鍵字規則，不用 LLM）。

這裡不碰 Discord，方便測試：輸入已經拿掉 @mention 的文字，輸出 Intent。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from . import timeparse
from .settings import find_county, note_categories

HELP_RE = r"^\s*(help|說明|幫助|怎麼用|使用說明|指令|功能|你會什麼|你可以做什麼)\s*[?？]?\s*$"
REMINDER_RE = r"^\s*記得|提醒|叫我|叫醒|通知我|記得要|記得叫|別忘了|不要忘記|別讓我忘|跟我說"
NOTE_RE = r"幫我記住|記住|記一下|記下來|記下|幫我記|筆記|備忘"
WEATHER_RE = r"天氣|下雨|氣溫|溫度|帶傘|幾度|冷不冷|熱不熱"
TIMER_RE = r"計時|倒數|碼表"
LIST_RE = (r"列出|列表|清單|有哪些|有什麼|有啥|查看|查詢|看一下|全部的|所有的|我的(提醒|記事|筆記|備忘)"
           r"|(什麼|啥|哪些)\s*[?？]?\s*$")
CANCEL_RE = r"(取消|刪除|刪掉|移除|不用了|拿掉)\s*(?:提醒|記事|筆記|備忘|靈感|想看|主題)?\s*((?:#?\d+[\s,，、]*)+)"
SNOOZE_RE = r"^\s*(延後|晚點|等等|等一下|再等|過一會)|延後"


@dataclass
class Intent:
    kind: str                       # help / reminder / timer / note / weather / list / cancel / delete_note / snooze / unknown
    text: str = ""
    when: datetime | None = None
    repeat: str | None = None
    seconds: int | None = None      # 計時器、延後
    category: str | None = None
    ids: list[int] = field(default_factory=list)
    scope: str = ""                 # list 用：reminders / notes / all
    county: str | None = None
    error: str | None = None


def route(text: str, now: datetime, settings: dict) -> Intent:
    text = text.strip()
    tp_kwargs = dict(
        period_times={"morning": settings["time_morning"], "noon": settings["time_noon"],
                      "afternoon": settings["time_afternoon"], "evening": settings["time_evening"],
                      "night": settings["time_night"]},
        date_only_time=settings["date_only_time"])
    cats = note_categories(settings)
    note_word = _note_category(text, cats)

    if not text or re.search(HELP_RE, text, re.I):
        return Intent("help")

    if m := re.search(CANCEL_RE, text):
        ids = [int(x) for x in re.findall(r"\d+", m.group(2))]
        if re.search(r"記事|筆記|備忘", text) or note_word:
            return Intent("delete_note", ids=ids)
        return Intent("cancel", ids=ids)

    if re.search(SNOOZE_RE, text):
        secs, _ = timeparse.parse_duration(text)
        if re.search(r"明天|明早", text):
            return Intent("snooze", text="tomorrow")
        return Intent("snooze", seconds=secs)

    is_reminder_word = bool(re.search(REMINDER_RE, text))
    if re.search(LIST_RE, text) and not re.match(r"^\s*(請|幫我)?\s*(提醒|叫)我", text):
        if re.search(r"提醒|行程|待辦", text):
            return Intent("list", scope="reminders")
        if note_word or re.search(r"記事|筆記|備忘|記住|記了", text):
            return Intent("list", scope="notes", category=note_word)

    if re.search(TIMER_RE, text):
        secs, spans = timeparse.parse_duration(text)
        if not secs:
            return Intent("timer", error="要計時多久？例如「計時 10 分鐘」")
        label = _clean(_remove_spans(text, spans), r"計時器?|倒數|碼表|開始|幫我|一下")
        return Intent("timer", seconds=secs, text=label)

    if re.search(WEATHER_RE, text) and not is_reminder_word:
        return Intent("weather", county=find_county(text))

    parsed = timeparse.parse(text, now, **tp_kwargs)

    if re.search(NOTE_RE, text) or (note_word and not is_reminder_word and not parsed.matched):
        category = note_word or cats[-1][0]
        body = _clean(text, NOTE_RE)
        body = _strip_category_word(body, cats)
        if not body:
            return Intent("note", error="要記什麼呢？例如「記住 靈感：…」")
        return Intent("note", category=category, text=body)

    if is_reminder_word or parsed.matched:
        if parsed.error:
            return Intent("reminder", error=parsed.error)
        if not parsed.when:
            return Intent("reminder", error="什麼時候提醒你？例如「明天下午3點提醒我交報告」")
        body = clean_reminder_text(parsed.strip(text))
        return Intent("reminder", text=body, when=parsed.when, repeat=parsed.repeat)

    if re.search(LIST_RE, text):
        return Intent("list", scope="all")
    return Intent("unknown")


# ---------- 文字整理 ----------

_LEAD = r"^[\s,，。.:：、!！~～\-]*((請|幫我|麻煩|記得|要|的時候|時|跟|和|再|就|然後|，|,)\s*)*"
_TAIL = r"(\s*(喔|哦|噢|唷|呦|啦|吧|呀|哈|謝謝|謝啦|thx|thanks|好嗎|好不好|一下|[,，。.!！~～?？]))+\s*$"


def clean_reminder_text(s: str) -> str:
    s = re.sub(r"(請|幫我|麻煩)?\s*(提醒|叫醒|叫|通知)\s*(我|一下)?(一下)?", " ", s, count=1)
    return _tidy(s)


def _clean(s: str, words: str) -> str:
    return _tidy(re.sub(words, " ", s, count=1))


def _tidy(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    prev = None
    while prev != s:
        prev = s
        s = re.sub(_LEAD, "", s)
        s = re.sub(_TAIL, "", s)
        s = s.strip()
    return s


def _note_category(text: str, cats) -> str | None:
    for name, kws in cats:
        if any(k and k in text for k in kws):
            return name
    return None


def _strip_category_word(body: str, cats) -> str:
    for _, kws in cats:
        for k in sorted(kws, key=len, reverse=True):
            if not k:
                continue
            pat_head = rf"^(我)?(有個|有一個|一個|新的)?{re.escape(k)}(的)?(作品|東西|主題)?\s*[:：，,、是]?\s*"
            pat_tail = rf"\s*[，,]?\s*(我)?{re.escape(k)}\s*$"
            new = re.sub(pat_head, "", body, count=1)
            if new == body:
                new = re.sub(pat_tail, "", body, count=1)
            if new != body and new.strip():
                return _tidy(new)
    return body


def _remove_spans(text: str, spans) -> str:
    for a, b in sorted(spans, reverse=True):
        text = text[:a] + " " + text[b:]
    return text
