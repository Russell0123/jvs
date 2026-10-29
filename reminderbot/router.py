"""把一句話分類成要做的事（關鍵字規則，不用 LLM）。

這裡不碰 Discord，方便測試：輸入已經拿掉 @mention 的文字，輸出 Intent。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from . import timeparse
from .settings import find_county, note_categories, topics, wish_lists, wish_verb
from .timeparse import NUM, cn2int

HELP_RE = r"^\s*(help|說明|幫助|怎麼用|使用說明|指令|功能|你會什麼|你可以做什麼)\s*[?？]?\s*$"
REMINDER_RE = r"^\s*記得|提醒|叫我|叫醒|通知我|問我|記得要|記得叫|別忘了|不要忘記|別讓我忘|跟我說"
NOTE_RE = r"幫我記住|記住|記一下|記下來|記下|幫我記|記起來|收著|存起來|筆記一下"
WEATHER_RE = r"天氣|下雨|氣溫|溫度|帶傘|幾度|冷不冷|熱不熱"
# 有「天氣」兩個字，或是在問問題（「我討厭下雨」不算）
WEATHER_ASK_RE = r"天氣|嗎|呢|\?|？|會不會|要不要|幾度|多少度|冷不冷|熱不熱|帶傘|如何|怎樣"
WEATHER_WEEK_RE = r"下週|下禮拜|下星期|這週|本週|這禮拜|一週|一周|未來幾天|這幾天|週末|周末"
LOCATION_RE = r"我\s*(?:現在|目前|人|這幾天|最近|今天)?\s*(?:人在|在|住在|住|搬到|搬去|到了|來到|待在)\s*(.+)"
TIMER_RE = r"計時|倒數|碼表"
LIST_RE = (r"列出|列表|清單|有哪些|有什麼|有啥|查看|查詢|看一下|全部的|所有的|我的(提醒|記事|筆記|備忘)"
           r"|(什麼|啥|哪些)\s*[?？]?\s*$")
CANCEL_ID_RE = r"(取消|刪除|刪掉|移除|不用了|拿掉)\s*(?:提醒|記事|筆記|備忘|靈感|想看|想買|想做|主題)?\s*((?:#?\d+[\s,，、]*)+)$"
CANCEL_VERB_RE = r"取消|刪掉|刪除|移除|拿掉|不用再|不要再|別再|不必再|不需要再"
SNOOZE_RE = (r"延到|延後|順延|改到|晚\S{0,5}?再|再(提醒|叫|跟)我|再提醒|再說|先不要")
PAYDAY_SET_RE = rf"發薪日?\s*(是|在|為)\s*(每個?月)?\s*({NUM})\s*(號|日)"
PAYDAY_RE = r"(每個?)?(發薪日|發薪那天|領薪水那天|領薪水的時候)"
RAIN_RE = r"下雨(天|的時候|的話|時)|會下雨(的話|時)?|雨天"
LATER_RE = r"晚點|待會|等一下|等等"
VAGUE_RE = r"有空(的時候|時)?|有時間(的時候)?|之後|以後|改天|找個?時間|找個\S{0,2}|哪天|回頭"
EVENTS = (r"出門|到家|回家|回到(?:宿舍|家|學校|公司|房間)?|吃完(?:飯|午餐|晚餐|早餐|東西)?|吃飽|"
          r"看完(?:影片|電影|書|劇)?|開完(?:會)?|下班|下課|放學|睡醒|睡覺|睡(?!醒)|起床|洗完澡|忙完|做完|寫完|弄完|運動完|回來")
EVENT_RE = (rf"({EVENTS})\s*((?:{NUM}|半)\s*個?\s*半?\s*(?:分鐘|分|小時|鐘頭))?\s*"
            r"(之前|以前|前|之後|以後|後|的時候|時|再)")
WISH_PREFIX = r"^(我)?(突然|最近|好|很|一直|有點|真的|超)?想"
WISH_PRE = r"(?:之後|以後|最近|有點|好|很|一直|突然|超|真的|還|也|蠻|滿|挺|偶爾|其實|有時候|再|好像)*"
WISH_WANT = r"(?:好想|想要|很想|想)"
WISH_MID = r"(?:找個?時間|有空(?:的時候)?|之後|以後|改天|再|去|來|一起|試著)*"
WISH_GENERIC = {"劇", "東西", "畫", "畫畫", "書", "電影", "影片", "動畫", "漫畫", "一下", "看", "看看", "什麼", "啥",
                "天氣", "你", "他", "她", "它", "這個", "那個", "點什麼", "些什麼", "東東"}


@dataclass
class Wish:
    category: str       # 清單名稱，例如「想畫」
    verb: str           # 「畫」
    text: str           # 「一隻穿西裝的柴犬」


def _verbs(settings) -> dict[str, str]:
    """動詞 → 清單名稱，長的動詞先比對（「畫畫」比「畫」先）。"""
    out = {}
    for name, verbs in wish_lists(settings):
        for v in verbs:
            out.setdefault(v, name)
    return dict(sorted(out.items(), key=lambda kv: -len(kv[0])))


def extract_wishes(text: str, settings: dict) -> list[Wish]:
    """「我之後想畫一隻柴犬」「有點想追孤獨搖滾」→ 願望。否定（不想、沒想）和問句不算。"""
    verbs = _verbs(settings)
    if not verbs:
        return []
    vre = "|".join(map(re.escape, verbs))
    out = []
    for m in re.finditer(rf"{WISH_PRE}{WISH_WANT}{WISH_MID}(?P<verb>{vre})(?P<obj>[^，。！？,.!?~～\n]*)", text):
        if re.search(r"[不沒別]\s*(太|是很|怎麼)?\s*$", text[max(0, m.start() - 4):m.start()]):
            continue
        obj = re.sub(r"(啊|耶|欸|誒|呢|吧|了|啦|喔|哦|說|…|\.)+$", "", m.group("obj")).strip()
        obj = re.sub(r"^(一下|看看)\s*", "", obj).strip()
        if not obj or obj in WISH_GENERIC or re.search(r"(嗎|呢|什麼|啥|哪些|哪個)$", obj):
            continue
        name, verb = verbs[m.group("verb")], m.group("verb")
        if not verb.startswith(wish_verb(name)):
            sep = " " if m.group("obj")[:1].isspace() else ""
            obj = verb + sep + obj  # 「想做」清單裡「規劃一次日本旅行」要留著動詞
        out.append(Wish(name, wish_verb(name), obj))
    return out


def _suggest_target(text: str, settings: dict) -> str | None:
    """「我最近可以畫什麼」「不知道畫什麼」「有推薦可以看的嗎」→ 要從哪個清單挑。"""
    verbs = _verbs(settings)
    if not verbs:
        return None
    v = "|".join(map(re.escape, verbs))
    pats = [rf"(?:可以|能|要|該|想)?\s*({v})\s*(?:什麼|啥|哪些|哪個|些什麼|點什麼)",
            rf"不知道\s*(?:要|該)?\s*({v})\s*(?:什麼|啥)",
            rf"推薦.{{0,12}}?({v})", rf"有什麼.{{0,8}}(?:可以|好|值得)\s*({v})"]
    for p in pats:
        if m := re.search(p, text):
            return verbs[m.group(1)]
    return None


@dataclass
class Intent:
    kind: str       # help / reminder / timer / note / wish / suggest / weather / list / cancel / delete_note
                    # / cancel_match / snooze / payday_set / unknown
    text: str = ""
    when: datetime | None = None
    repeat: str | None = None
    phrase: str = ""                # 使用者講時間的原話，回覆照著講
    dated: bool = True              # 原話有沒有講到哪一天
    verb: str = "提醒"              # 提醒／叫／問
    seconds: int | None = None      # 計時器、延後
    category: str | None = None
    ids: list[int] = field(default_factory=list)
    scope: str = ""                 # list／cancel_match 用：reminders / notes / all
    county: str | None = None
    town: str = ""                  # 鄉鎮區（天氣、改地點）
    mode: str = ""                  # 天氣：today / tomorrow / week / weekend（空白＝自動）
    error: str | None = None
    ask: str = ""                   # 需要問時間：event（看事件）／vague（太模糊）／none（沒講）
    event: str = ""                 # 例如「吃完飯」
    offset: int = 0                 # 事件後幾秒（「到家一小時後」）
    max_count: int | None = None    # 重複提醒最多幾次；單次提醒 = 總共提醒幾次
    until: datetime | None = None   # 重複提醒到什麼時候
    limit_label: str = ""           # 「共 4 次」「持續一週」「只有今天」
    rain: bool = False              # 下雨天才提醒
    payday: bool = False            # 發薪日提醒
    day: int | None = None          # payday_set：幾號；cancel_match：0 今天 / 1 明天
    keyword: str = ""               # cancel_match：用內容找
    all: bool = False               # cancel_match：所有
    wishes: list = field(default_factory=list)   # wish：記下的願望
    topic: str = ""                 # 常見提醒（休息、喝水…），回覆和觸發用固定說法


def route(text: str, now: datetime, settings: dict) -> Intent:
    text = text.strip()
    tp_kwargs = dict(
        period_times={"morning": settings["time_morning"], "noon": settings["time_noon"],
                      "afternoon": settings["time_afternoon"], "evening": settings["time_evening"],
                      "night": settings["time_night"]},
        date_only_time=settings["date_only_time"], few_days=int(settings.get("few_days", 2)))
    cats = note_categories(settings)

    if not text or re.search(HELP_RE, text, re.I):
        return Intent("help")

    if m := re.search(PAYDAY_SET_RE, text):
        if not re.search(REMINDER_RE, text):
            return Intent("payday_set", day=cn2int(m.group(3)))

    if m := re.search(CANCEL_ID_RE, text):
        ids = [int(x) for x in re.findall(r"\d+", m.group(2))]
        if re.search(r"記事|筆記|備忘", text) or _note_category(text, cats):
            return Intent("delete_note", ids=ids)
        return Intent("cancel", ids=ids)

    cv, rv = re.search(CANCEL_VERB_RE, text), re.search(REMINDER_RE, text)
    if cv and (not rv or cv.start() <= rv.start()):
        return _cancel_match(text)

    if (target := _suggest_target(text, settings)) and not rv:
        return Intent("suggest", category=target)

    if re.search(TIMER_RE, text):
        secs, spans = timeparse.parse_duration(text)
        if not secs:
            return Intent("timer", error="要計時多久？例如「計時 10 分鐘」")
        label = _clean(_remove_spans(text, spans), r"計時器?|倒數|碼表|開始|幫我|一下")
        return Intent("timer", seconds=secs, text=label)

    is_reminder_word = bool(rv)
    if (m := re.search(LOCATION_RE, text)) and not is_reminder_word and (county := find_county(m.group(1))):
        return Intent("location", county=county, town=_town(m.group(1), county, bare=True))

    if re.search(WEATHER_RE, text) and re.search(WEATHER_ASK_RE, text) and not is_reminder_word:
        mode = ("weekend" if re.search(r"週末|周末", text) else "week") if re.search(WEATHER_WEEK_RE, text) else \
            "tomorrow" if re.search(r"明天|明早|明晚|明日", text) else \
            "today" if re.search(r"今天|今日|現在|今晚|等等|待會", text) else ""
        county = find_county(text)
        return Intent("weather", county=county, town=_town(text, county) if county else "", mode=mode,
                      rain=bool(re.search(r"雨|帶傘", text)))

    body, limits = _extract_limits(text, now)
    event = re.search(EVENT_RE, body)
    vague_full = re.search(VAGUE_RE, body)

    if re.search(SNOOZE_RE, body):
        if (it := _snooze(body, now, tp_kwargs, settings)) is not None:
            return it

    # 「開完會半小時後」整段是事件，不要被當成「半小時後」
    parsed = timeparse.parse(body[:event.start()] + " " * (event.end() - event.start()) + body[event.end():]
                             if event else body, now, **tp_kwargs)
    note_word = _note_category(text, cats)
    if re.search(LIST_RE, text) and not re.match(r"^\s*(請|幫我)?\s*(提醒|叫)我", text):
        if re.search(r"提醒|行程|待辦", text):
            return Intent("list", scope="reminders")
        wl = next((n for n, _ in wish_lists(settings) if n in text), None)
        if note_word or wl or re.search(r"記事|筆記|備忘|記住|記了|清單", text):
            return Intent("list", scope="notes", category=note_word or wl)
    if not is_reminder_word and (wishes := extract_wishes(text, settings)):
        return Intent("wish", wishes=wishes)
    if not is_reminder_word and (re.search(NOTE_RE, text) or (note_word and not parsed.matched)):
        return _note(text, note_word, cats)


    later = re.search(LATER_RE, body)
    # 沒說「提醒」也算提醒：講了時刻／相對時間（「12點睡覺」「半小時後吃飯」），或內容是常見提醒
    loose_topic = match_topic(clean_reminder_text(parsed.strip(body)), settings) if parsed.matched else None
    if not (is_reminder_word or later or (parsed.matched and (parsed.timed or loose_topic))):
        if re.search(LIST_RE, text):
            return Intent("list", scope="all")
        return Intent("unknown")

    it = Intent("reminder", verb=_verb(body), **limits)
    leftover = parsed.strip(body)
    if event:
        leftover = leftover.replace(event.group(0), " ", 1)
    for pat in (RAIN_RE, PAYDAY_RE):
        leftover = re.sub(pat, " ", leftover, count=1)
    vague = re.search(VAGUE_RE, leftover)
    if vague:
        leftover = leftover.replace(vague.group(0), " ", 1)
    if later and not parsed.matched:
        leftover = leftover.replace(later.group(0), " ", 1)
    content = clean_reminder_text(leftover)
    it.text = second_person(content)
    if topic := match_topic(content, settings):
        it.topic, it.text = topic.name, topic.name

    if re.search(RAIN_RE, body):
        it.rain = True
        return it
    if m := re.search(PAYDAY_RE, body):
        it.payday = True
        it.repeat = "payday" if m.group(1) else None
        return it
    if event:
        it.ask, it.event = "event", event.group(1)
        if event.group(2) and event.group(3) in ("之後", "以後", "後"):
            it.offset = timeparse.parse_duration(event.group(2))[0] or 0
        return it
    if vague:
        it.ask, it.event = "vague", (vague_full.group(0) if vague_full else vague.group(0)).strip()
        return it
    if later and not parsed.matched:
        it.seconds = int(settings.get("btn_later_minutes", 90)) * 60
        it.when, it.phrase = now + timedelta(seconds=it.seconds), later.group(0)
        return it
    if parsed.error:
        it.error = parsed.error
        return it
    if not parsed.when:
        it.ask = "none"
        return it
    it.when, it.repeat, it.phrase, it.dated = parsed.when, parsed.repeat, parsed.phrase(body), parsed.dated
    if it.topic == "休息" and it.when.hour == 12 and not re.search(r"中午|下午|午", it.phrase):
        # 「12點睡覺」是半夜 12 點
        midnight = datetime.combine(it.when.date(), time(0, 0), it.when.tzinfo)
        it.when = midnight if midnight > now else midnight + timedelta(days=1)
    if it.repeat and it.repeat.startswith("interval:") and not it.until:
        # 一天之內的循環（每兩小時、每 30 分鐘）只循環當天
        it.until = datetime.combine(it.when.astimezone(now.tzinfo).date(), time(23, 59, 59), now.tzinfo)
    return it


TOPIC_FILLER = r"^(我|你|要|去|該|來|得|先|好好|準備)+|(一下|一會|一會兒|了|囉|吧|啦)+$"


def match_topic(content: str, settings: dict):
    """內容基本上就是「睡覺」「喝點水」「吃午餐」這種常見提醒時，回傳那個 Topic。"""
    c = re.sub(TOPIC_FILLER, "", content.replace(" ", "")).strip()
    c = re.sub(TOPIC_FILLER, "", c)
    if not c:
        return None
    for t in topics(settings):
        for k in sorted(t.keywords, key=len, reverse=True):
            if c == k or (c.startswith(k) and len(c) - len(k) <= 2) or (c.endswith(k) and len(c) - len(k) <= 1):
                return t
    return None


# ---------- 各種意圖 ----------

def _extract_limits(text: str, now: datetime) -> tuple[str, dict]:
    """拿掉「共四次」「持續一週」「只問今天」這類限制，回傳 (剩下的句子, 限制)。"""
    out: dict = {}
    t = re.sub(r"[，,]?\s*(如果|要是)\s*我?\s*都?\s*沒(有)?(理|回|反應|回應)\s*你?\s*(就算了|就好|的話)?", " ", text)
    t = re.sub(r"(提醒|叫|問)(我)?一次(?=[，,。\s]|$)", r"\1\2", t)
    stop = r"\s*(就|後|以後|之後)?\s*(自動)?\s*(取消|停止|結束|停|算了|好)?"
    if m := re.search(rf"(共|總共|一共)\s*(提醒|叫|問)?\s*({NUM})\s*次{stop}", t):
        out["max_count"] = cn2int(m.group(3))
        t = t[:m.start()] + " " + t[m.end():]
    elif m := re.search(rf"(提醒|叫|問)\s*(我)?\s*({NUM})\s*次{stop}", t):
        out["max_count"] = cn2int(m.group(3))
        t = t[:m.start()] + f" {m.group(1)}{m.group(2) or ''} " + t[m.end():]
    elif m := re.search(rf"({NUM})\s*次\s*(後|以後|之後|為止|就好){stop}", t):
        out["max_count"] = cn2int(m.group(1))
        t = t[:m.start()] + " " + t[m.end():]
    if out.get("max_count", 2) < 2:
        out.pop("max_count")
    if "max_count" in out:
        out["limit_label"] = f"共 {out['max_count']} 次"

    end_of = lambda d: datetime.combine(d, time(23, 59, 59), now.tzinfo)  # noqa: E731
    if m := re.search(rf"[，,]?\s*持續\s*({NUM}|半)?\s*個?\s*(天|週|周|星期|禮拜|月)", t):
        n = 1 if not m.group(1) else (0.5 if m.group(1) == "半" else cn2int(m.group(1)))
        days = {"天": 1, "月": 30}.get(m.group(2), 7) * n
        out["until"] = end_of(now.date() + timedelta(days=max(0, int(days) - 1)))
        out["limit_label"] = (out.get("limit_label", "") + f" 持續{m.group(0).split('持續')[1].strip()}").strip()
        t = t[:m.start()] + " " + t[m.end():]
    elif m := re.search(r"[，,]?\s*(只(有)?(問|提醒|叫)?今天|今天就好|今天而已|今天內)", t):
        out["until"] = end_of(now.date())
        out["limit_label"] = (out.get("limit_label", "") + " 只有今天").strip()
        t = t[:m.start()] + " " + t[m.end():]
    elif m := re.search(rf"[，,]?\s*到\s*({NUM})\s*(號|日)\s*(為止)?", t):
        d = cn2int(m.group(1))
        target = timeparse._add_months(now.date().replace(day=1), 0, d)
        if target < now.date():
            target = timeparse._add_months(now.date().replace(day=1), 1, d)
        out["until"] = end_of(target)
        out["limit_label"] = (out.get("limit_label", "") + f" 到 {target.month}/{target.day}").strip()
        t = t[:m.start()] + " " + t[m.end():]
    return re.sub(r"\s+", " ", t).strip(), out


SNOOZE_FILLER = (r"如果\s*我?\s*(還)?\s*沒(有)?\s*(做完|完成|弄完|做|處理)|這個|這件事|那件事|那個|提醒|一次|幫我|"
                 r"先不要|不要|延到|延後|順延|改到|叫|跟|說|一下|就|好|再|晚|吧|了|我|這|到|[，,。!！~～\s]")


def _snooze(body: str, now: datetime, tp_kwargs: dict, settings: dict) -> Intent | None:
    """「延到明天早上」「晚兩個小時再提醒」「這件事下週一再說」→ 延後；句子裡還有別的內容就不是延後。"""
    event = re.search(EVENT_RE, body)
    rest = re.sub(EVENT_RE, " ", body) if event else body
    parsed = timeparse.parse(rest, now, **tp_kwargs)
    leftover = parsed.strip(rest)
    secs, dur_phrase = None, ""
    if not parsed.when:
        secs, spans = timeparse.parse_duration(leftover)
        dur_phrase = _span_text(leftover, spans)
        leftover = _remove_spans(leftover, spans)
    if re.sub(SNOOZE_FILLER, "", leftover):
        return None
    if event:
        return Intent("snooze", ask="event", event=event.group(1))
    if parsed.when:
        return Intent("snooze", when=parsed.when, phrase=parsed.phrase(rest), dated=parsed.dated)
    if secs:
        return Intent("snooze", seconds=secs, when=now + timedelta(seconds=secs), phrase=dur_phrase + "後")
    if re.search(r"晚點|等等|待會|等一下", body):
        secs = int(settings.get("btn_later_minutes", 90)) * 60
        return Intent("snooze", seconds=secs, when=now + timedelta(seconds=secs), phrase="晚點")
    return Intent("snooze", ask="none")


def _cancel_match(text: str) -> Intent:
    notes = bool(re.search(r"想法|記事|筆記|靈感|點子|備忘|清單|願望|想[畫看買學去吃做]", text)) and "提醒" not in text
    day = 0 if re.search(r"今天|今日", text) else 1 if re.search(r"明天", text) else None
    everything = bool(re.search(r"所有|全部|通通|都", text))
    kw = re.sub(CANCEL_VERB_RE, " ", text)
    kw = re.sub(r"提醒|記事|筆記|想法|靈感|點子|備忘|清單|願望|那個|這個|那些|這些|剛剛|剛才|之前|有關|相關|所有|全部|"
                r"通通|今天|今日|明天|一下|幫我|麻煩|等等|等一下|我|的|跟|和|都|了|吧|再|啦|喔|[，,。!！\s]", "", kw)
    return Intent("cancel_match", scope="notes" if notes else "reminders", keyword=kw, day=day, all=everything)


def _note(text: str, note_word: str | None, cats) -> Intent:
    category = note_word or cats[-1][0]
    body = _clean(text, NOTE_RE)
    body = re.sub(r"^(先|幫我|麻煩|請)+", "", body).strip()
    body = _strip_category_word(body, cats)
    if not body or re.fullmatch(r"(這個|那個|剛剛那個)?\s*(想法|點子|東西|靈感)?", body):
        return Intent("note", error="要記什麼？")
    return Intent("note", category=category, text=body)


# ---------- 文字整理 ----------

_LEAD = r"^[\s,，。.:：、!！~～\-]*((請|幫我|麻煩|記得|我要|我得|我該|我會|我應該|要|的時候|跟|和|再|就|然後|，|,)\s*)*"
_TAIL = r"(\s*(喔|哦|噢|唷|呦|啦|吧|呀|哈|謝謝|謝啦|thx|thanks|好嗎|好不好|一下|[,，。.!！~～?？]))+\s*$"


def clean_reminder_text(s: str) -> str:
    s = re.sub(r"(請|幫我|麻煩)?\s*(提醒|叫醒|叫|通知|問|跟)\s*(我|一下)?(說)?(一下)?", " ", s, count=1)
    return _tidy(s)


def second_person(s: str) -> str:
    """提醒內容裡的「我」換成「你」（「問我有沒有出門」→「有沒有出門」已經拿掉動詞，剩下的我→你）。"""
    return s.replace("我", "你")


def _town(text: str, county: str, bare: bool = False) -> str:
    """「台中市西屯區」→「西屯區」；bare=True 時「台中西屯」也接受（回傳「西屯」，查預報時比對開頭）。"""
    t = text.replace("台", "臺")
    if county in t:
        rest = t[t.find(county) + len(county):]
    elif county[:2] in t:
        rest = re.sub(r"^[市縣]", "", t[t.find(county[:2]) + 2:])
    else:
        return ""
    if m := re.match(r"([\u4e00-\u9fff]{1,3}?[區鄉鎮市])", rest):
        return m.group(1)
    if bare and (m := re.match(r"([\u4e00-\u9fff]{2})(?:了|啦|喔|囉)?$", rest.strip())):
        return m.group(1)
    return ""


def _verb(s: str) -> str:
    if m := re.search(r"(叫醒|叫|問|通知)\s*我", s):
        return {"叫醒": "叫", "通知": "提醒"}.get(m.group(1), m.group(1))
    return "提醒"


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
    for name, kws in cats:
        for k in sorted(kws, key=len, reverse=True):
            if not k or k not in body:
                continue
            if k.startswith("想") and k != name:
                # 「我突然想畫柴犬」→ 只拿掉「我突然想」，留下「畫柴犬」
                new = re.sub(WISH_PREFIX, "", body, count=1)
                return _tidy(new) if new.strip() else body
            pat_head = (rf"^(我)?(突然|最近|好|很|一直|有點|真的)?(有個|有一個|一個|新的)?{re.escape(k)}(的)?"
                        rf"(作品|東西|主題)?\s*[:：，,、是]?\s*")
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


def _span_text(text: str, spans) -> str:
    return "".join(text[a:b] for a, b in sorted(spans)).strip()


def fuzzy_match(keyword: str, text: str) -> bool:
    """「買鍵盤」對得到「一台機械鍵盤」：整段相符，或一半以上的兩字片段出現在內容裡。"""
    if not keyword:
        return False
    if keyword in text:
        return True
    grams = {keyword[i:i + 2] for i in range(len(keyword) - 1)}
    hits = sum(g in text for g in grams)
    return hits > 0 and hits * 2 >= len(grams)

