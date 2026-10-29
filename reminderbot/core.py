"""助理的核心邏輯，不碰 Discord、不碰網路。

輸入：一則訊息、按下一個按鈕、或「現在幾點了」（tick）。
輸出：要做的動作清單（送訊息、改訊息、查天氣）。
正式的 Discord bot（bot.py）和測試網頁（sim/）都用同一份。

回覆的口吻規則（文案規格 v1）：短句、不重述使用者講的內容、不加括號說明、
同一組說法最近用過的 3 句不會馬上再用。說法都在設定裡，可以自己改。
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from . import timeparse, weather
from .router import REMINDER_RE, Intent, clean_reminder_text, extract_wishes, fuzzy_match, route
from .settings import find_county, phrases, topics, wish_acks, wish_verb

HELP = """**我會做這些事**
👋 **叫我**：@賈維斯，或句子裡提到賈維斯、Jarvis
⏰ **提醒**：`明天下午3點提醒我交報告`、`這幾天提醒我訂車票`、`12點睡覺`
🔁 **重複**：`每天早上8點提醒我吃藥`、`每週三和週五提醒我運動`
👥 **提醒別人**：`明天中午提醒 @某人 開會`
☔ **看情況**：`下雨天提醒我帶傘`、`發薪日提醒我存錢`
⏱️ **計時**：`計時 25 分鐘`
🎨 **願望**：聊天時說 `我想畫…`、`想買…`，之後問 `我最近可以畫什麼`
📝 **記住**：`記住 靈感：…`
📋 **查詢**：`我有哪些提醒`
🗑️ **取消**：`不用再提醒我喝水了`、`取消今天所有提醒`
💤 **延後**：`後天再提醒我`、`晚兩個小時再提醒`
↩️ **回覆提醒**：`好`、`不用了`、`改成十分鐘後`
🌤️ **天氣**：`天氣`、`明天會下雨嗎`、`我在台中`
⌨️ **指令**：`/說明`、`/設定`、`/天氣`"""

DRAFT_MINUTES = 30          # 問了時間之後，多久內回答都算數
FAR_FUTURE = 4102444800     # 2100 年：下雨天提醒這種「看情況」的，平常不會到期
PICKS = [("tonight", "今晚"), ("tmrm", "明天早上"), ("tmra", "明天下午"), ("wknd", "這週末")]
SHORT_WHEN = 4              # 使用者講的時間不超過幾個字（「這幾天」「月底」），回覆才照著講


# ---------- 輸入／輸出 ----------

@dataclass
class User:
    id: int
    name: str = ""
    role_ids: set[int] = field(default_factory=set)


@dataclass
class Incoming:
    author: User
    text: str                                   # 原始訊息（含 <@id> 這類 mention）
    channel_id: int
    message_id: int = 0
    mention_user_ids: list[int] = field(default_factory=list)   # 不含 bot 自己、不含其他 bot
    mention_role_ids: list[int] = field(default_factory=list)   # 不含 bot 自己的身分組
    reply_to_bot_message_id: int | None = None  # 回覆的是 bot 的哪則訊息
    mentioned: bool = True                      # 有沒有 @ bot；沒有的話只聽願望（而且只聽指定的人）


@dataclass
class Button:
    custom_id: str
    label: str
    emoji: str = ""
    style: str = "secondary"                    # success / secondary / danger
    url: str = ""                               # 有網址就是連結按鈕


@dataclass
class Send:
    channel_id: int
    text: str
    buttons: list[Button] = field(default_factory=list)
    ping: bool = False                          # True 才真的 @ 到人
    reply_to: int | None = None                 # 回覆哪則訊息
    reminder_id: int | None = None              # 送出後要記下 message_id（呼叫 core.sent）
    embed: dict | None = None                   # 卡片：title / description / fields / color / footer
    ephemeral: bool = False                     # 只有對方看得到（/ 指令）


@dataclass
class Edit:
    channel_id: int
    message_id: int
    append: str = ""                            # 在原訊息後面加一行
    strike: bool = False                        # 原訊息加刪除線
    clear_buttons: bool = True


@dataclass
class NeedWeather:
    """請轉接層去抓天氣，抓到後呼叫 core.weather_result（失敗呼叫 core.weather_error）。"""
    county: str
    channel_id: int | None                      # None = 提醒頻道
    reply_to: int | None = None
    purpose: str = "show"                       # show（有人問）／daily（每天早上）／monitor（檢查變化）
    town: str = ""
    mode: str = ""                              # today / tomorrow / week / weekend / ""（自動）
    rain_q: bool = False                        # 問「會不會下雨」
    rain_check: bool = False                    # 順便檢查下雨天提醒
    post: bool = True                           # daily：要不要貼出來
    ephemeral: bool = False                     # / 指令：只給對方看

    @property
    def need_current(self) -> bool:
        return self.purpose != "monitor"

    @property
    def need_week(self) -> bool:
        return self.mode in ("week", "weekend")


@dataclass
class PressResult:
    ephemeral: str | None = None                # 只給按的人看的訊息（不改原訊息）
    append: str = ""
    strike: bool = False
    keep_buttons: bool = False
    actions: list = field(default_factory=list)  # 按完還要做的事（例如送出確認訊息）


def mention_str(target: str) -> str:
    kind, _, tid = target.partition(":")
    return f"<@&{tid}>" if kind == "r" else f"<@{tid}>"


class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def fill(template: str, **values) -> str:
    return template.format_map(_SafeDict(values)).strip()


def fmt_seconds(s: int) -> str:
    h, rem = divmod(int(s), 3600)
    m, sec = divmod(rem, 60)
    parts = [f"{h} 小時" if h else "", f"{m} 分鐘" if m else "", f"{sec} 秒" if sec else ""]
    return " ".join(p for p in parts if p) or "0 秒"


def say(template: str, **values) -> str:
    """套用口吻句；內容是空的就把「：{text}」這類片段收乾淨。"""
    out = fill(template, **values)
    out = re.sub(r"[：:]\s*([。！～!?？])", r"\1", out)
    return re.sub(r"\s{2,}", " ", out).strip()


class Assistant:
    def __init__(self, store, tz, owner_id: int | None = None, home_channel_id: int | None = None):
        self.store = store
        self.tz = tz
        self.owner_id = owner_id                 # 沒設定 wish_listen_ids 時，只聽這個人的願望
        self.home_channel_id = home_channel_id   # 提醒一律發到這個頻道（在哪裡 @ 都一樣）
        self.last_intent: Intent | None = None   # 測試網頁顯示「判斷結果」用

    def _home(self, msg: Incoming) -> int:
        return self.home_channel_id or msg.channel_id

    def _where(self, msg_channel: int, home: int) -> str:
        """在別的頻道設的提醒，回覆下面標一下會在哪個頻道響。"""
        return f"\n-# → <#{home}>" if home != msg_channel else ""

    def _ch(self, message_id: int, fallback: int) -> int:
        """這則訊息在哪個頻道（確認訊息可能在別的頻道，提醒訊息在提醒頻道）。"""
        return self.store.get_meta(f"msgch:{message_id}") or fallback

    def listens_to(self, user_id: int, s: dict) -> bool:
        ids = {int(x) for x in re.findall(r"\d+", str(s.get("wish_listen_ids", "")))}
        return user_id in (ids or ({self.owner_id} if self.owner_id else set()))

    # ---------- 挑說法：隨機，但最近用過的不馬上重複 ----------
    def pick(self, key: str, pool: list[str], rng, **values) -> str:
        pool = [p for p in pool if p] or [""]
        recent = self.store.get_meta(f"recent:{key}", []) or []
        fresh = [p for p in pool if p not in recent] or [p for p in pool if p != (recent[-1] if recent else None)] \
            or pool
        choice = rng.choice(fresh)
        keep = max(0, min(3, len(pool) - 1))
        self.store.set_meta(f"recent:{key}", (recent + [choice])[-keep:] if keep else [])
        return say(choice, **values)

    def phrase(self, s: dict, key: str, rng, **values) -> str:
        return self.pick(key, phrases(s, key), rng, **values)

    def _topic(self, name: str, s: dict):
        return next((t for t in topics(s) if t.name == name), None) if name else None

    # ---------- 收訊息 ----------
    def is_owner(self, user_id: int) -> bool:
        return self.owner_id is None or user_id == self.owner_id

    def _names_re(self, s: dict) -> str:
        names = [n.strip() for n in re.split(r"[,，、\s]+", str(s["bot_names"])) if n.strip()]
        return "|".join(rf"(?<![a-z]){re.escape(n)}(?![a-z])" if n.isascii() else re.escape(n)
                        for n in sorted(names, key=len, reverse=True)) or r"(?!x)x"

    def _greet_re(self, s: dict) -> str:
        words = [w.strip() for w in re.split(r"[,，、\s]+", str(s["greet_words"])) if w.strip()]
        return "|".join(rf"(?<![a-z]){re.escape(w)}(?![a-z])" if w.isascii() else re.escape(w)
                        for w in sorted(words, key=len, reverse=True)) or r"(?!x)x"

    def _strip_call(self, text: str, s: dict) -> str:
        """拿掉名字和開頭的招呼：「嘿賈維斯，明天提醒我…」→「明天提醒我…」。"""
        t = re.sub(self._names_re(s), " ", text, flags=re.I)
        lead = rf"^[\s,，.。!！~～、]*({self._greet_re(s)})?[\s,，.。!！~～、]*"
        prev = None
        while prev != t:
            prev, t = t, re.sub(lead, "", t, count=1, flags=re.I)
        return t.strip()

    def handle(self, msg: Incoming, now: datetime) -> list:
        s = self.store.settings()
        text = re.sub(r"<@[!&]?\d+>", " ", msg.text).strip()
        rng = random.Random(msg.message_id or int(now.timestamp()))
        owner = self.is_owner(msg.author.id)
        called = bool(re.search(self._names_re(s), text, re.I))

        if not msg.mentioned and not called:
            self.last_intent = None
            # 主人回答 bot 的問句（「8點」）不用 @
            draft = self._draft_for(msg, now) if owner else None
            if draft and (answer := self._time_answer(text, Intent("unknown"), now, s)) is not None:
                self.last_intent = Intent("reminder", when=answer.when, repeat=answer.repeat, text="回答時間")
                return self._finish_draft(draft, answer.when, answer.repeat, answer.phrase(text), answer.dated,
                                          msg.channel_id, msg.message_id, now, rng)
            # 其他沒在叫它的：只聽指定成員的「我想畫…」這類願望
            if not (s["feature_note"] and self.listens_to(msg.author.id, s)):
                return []
            wishes = extract_wishes(text, s)
            if not wishes:
                return []
            self.last_intent = Intent("wish", wishes=wishes)
            self._save_wishes(msg.author.id, wishes, now)
            return self._reply(msg, self._wish_ack(wishes, rng)) if s["passive_ack"] else []

        greeted = bool(re.search(self._greet_re(s), text, re.I))
        clean = self._strip_call(text, s)

        if msg.reply_to_bot_message_id and clean:
            out = self._reply_to_reminder(msg, clean, now, s, rng)
            if out is not None:
                return out

        it = route(clean, now, s) if clean else Intent("unknown")
        self.last_intent = it

        draft = self._draft_for(msg, now) if owner else None
        if draft and clean:
            if re.fullmatch(r"(算了|不用了|先不要|不要了|取消|沒事)[。！!]?", clean):
                self.store.update_reminder(draft["id"], status="cancelled")
                return self._reply(msg, self.phrase(s, "draft_cancel_phrases", rng))
            answer = self._time_answer(clean, it, now, s)
            if answer:
                self.last_intent = Intent("reminder", when=answer.when, repeat=answer.repeat, text="回答時間")
                return self._finish_draft(draft, answer.when, answer.repeat, answer.phrase(clean), answer.dated,
                                          msg.channel_id, msg.message_id, now, rng)

        if it.kind != "unknown":
            if s["owner_only"] and not owner:
                self.last_intent = Intent("refuse", text="不是主人")
                return self._reply(msg, self.phrase(s, "refuse_phrases", rng))
            return self._dispatch(msg, it, s, now, rng)
        if greeted or not clean:
            self.last_intent = Intent("greet")
            return self._reply(msg, self.greet(s, rng))
        if owner and msg.mentioned:
            return self._dispatch(msg, it, s, now, rng)
        return []           # 只是聊天提到名字，不回

    def greet(self, s: dict, rng) -> str:
        if rng.random() * 100 < float(s["greet_rare_percent"]):
            return self.phrase(s, "greet_rare_phrases", rng)
        return self.phrase(s, "greet_phrases", rng)

    def slash(self, name: str, user: User, now: datetime, channel_id: int, arg: str = "",
              settings_url: str = "") -> list:
        """/ 指令：回覆只有本人看得到；不是主人就拒絕。"""
        s = self.store.settings()
        rng = random.Random(int(now.timestamp()))

        def say_(text, buttons=None):
            return [Send(channel_id, text, buttons or [], ephemeral=True)]
        if s["owner_only"] and not self.is_owner(user.id):
            return say_(self.phrase(s, "refuse_phrases", rng))
        if name == "help":
            return say_(HELP)
        if name == "settings":
            if not settings_url:
                return say_("設定網頁還沒開，主機上跑 deploy/public-web.sh。")
            return say_(self.phrase(s, "settings_link_phrases", rng), [Button("", "打開設定", url=settings_url)])
        if name == "weather":
            county = find_county(arg) if arg else None
            county, town = (county, "") if county else self.location(user.id, s)
            return [NeedWeather(county, channel_id, town=town, ephemeral=True,
                                mode="tomorrow" if "明" in arg else "today" if "今" in arg else "")]
        return say_("沒有這個指令。")

    def _reply_to_reminder(self, msg: Incoming, text: str, now: datetime, s: dict, rng) -> list | None:
        """回覆了 bot 的某則提醒訊息（不用 @）：取消、完成或延後「那一個」。不是這幾種就照一般判斷。"""
        rid = self.store.get_meta(f"msg:{msg.reply_to_bot_message_id}")
        rem = self.store.get_reminder(int(rid)) if rid else None
        if not rem or rem["status"] == "draft" or not can_touch(rem, msg.author):
            return None
        mid, t = msg.reply_to_bot_message_id, text.strip()
        if re.search(r"取消|不用了|不要了|刪掉|刪除|拿掉|算了|不需要|別提醒|不用提醒", t) and len(t) <= 12:
            parent = self.store.get_reminder(rem["parent_id"]) if rem["parent_id"] else None
            target = parent if parent and parent["status"] == "pending" else rem
            self.last_intent = Intent("cancel", ids=[target["id"]])
            out = self._cancel_now(msg, "reminders", [target["id"]], "", rng, s)
            if rem["id"] != target["id"]:
                self.store.update_reminder(rem["id"], status="done")
            return [Edit(msg.channel_id, mid, strike=True), *out]
        if re.fullmatch(r"(好|好的|好了|收到|ok|okay|完成|做完了|弄好了|弄完了|知道了|了解|👍|✅)[。！!～~]*", t, re.I):
            self.last_intent = Intent("cancel", text="完成")
            self.store.update_reminder(rem["id"], status="done")
            return [Edit(msg.channel_id, mid, append="-# ✅"), *self._reply(msg, self.phrase(s, "done_phrases", rng))]
        if t in (s["btn_later_label"], s["btn_tomorrow_label"]):
            code = str(s["btn_later_minutes"]) if t == s["btn_later_label"] else "tmr"
            when = self._choice_dt(code, now, s)
            self.last_intent = Intent("snooze", when=when, phrase=t)
            return self._apply_snooze(rem, when, "", msg.channel_id, msg.message_id, s, rng)
        it = route(t, now, s)
        when, phrase, dated = None, "", True
        if it.kind == "snooze" and it.when:
            when, phrase, dated = it.when, it.phrase, it.dated
        elif (answer := self._time_answer(t, it, now, s)) is not None:
            when, phrase, dated = answer.when, answer.phrase(t), answer.dated
        elif it.kind == "reminder" and it.when and not it.text:
            when, phrase, dated = it.when, it.phrase, it.dated
        if when is None:
            return None
        self.last_intent = Intent("snooze", when=when, phrase=phrase, dated=dated)
        return self._apply_snooze(rem, when, self._echo(phrase, when, dated, now), msg.channel_id, msg.message_id,
                                  s, rng)

    def _time_answer(self, text: str, it: Intent, now: datetime, s: dict):
        """「明天晚上」「8點吧」這種只有時間的句子，當成在回答問句。又講了「提醒…」就是新的指令。"""
        if re.search(REMINDER_RE, text) or it.limit_label or it.kind not in ("reminder", "unknown"):
            return None
        parsed = timeparse.parse(text, now, period_times={
            "morning": s["time_morning"], "noon": s["time_noon"], "afternoon": s["time_afternoon"],
            "evening": s["time_evening"], "night": s["time_night"]}, date_only_time=s["date_only_time"],
            few_days=int(s["few_days"]))
        if not parsed.when or re.sub(r"[\s，,。！!~～吧啦喔好的左右差不多大概]+", "", clean_reminder_text(parsed.strip(text))):
            return None
        return parsed

    def _reply(self, msg: Incoming, text: str, buttons=None, reminder_id=None) -> list:
        return [Send(msg.channel_id, text[:2000], buttons or [], reply_to=msg.message_id, reminder_id=reminder_id)]

    def _dispatch(self, msg: Incoming, it: Intent, s: dict, now: datetime, rng) -> list:
        reply = lambda text, buttons=None, rid=None: self._reply(msg, text, buttons, rid)  # noqa: E731
        ts = int(now.timestamp())

        if it.error:
            return reply(it.error)
        if it.kind == "help":
            return reply(HELP)

        if it.kind == "reminder":
            if not s["feature_reminder"]:
                return reply("提醒功能關著喔。")
            return self._reminder(msg, it, s, now, rng)

        if it.kind == "timer":
            if not s["feature_timer"]:
                return reply("計時功能關著喔。")
            due = now + timedelta(seconds=it.seconds)
            rid = self.store.add_reminder(
                kind="timer", channel_id=self._home(msg), creator_id=msg.author.id,
                targets=[f"u:{msg.author.id}"], text=it.text, due_at=int(due.timestamp()), now=ts,
                extra={"duration": fmt_seconds(it.seconds)})
            return reply(self.phrase(s, "timer_phrases", rng, duration=fmt_seconds(it.seconds))
                         + self._where(msg.channel_id, self._home(msg)), [cancel_button(rid)])

        if it.kind == "note":
            if not s["feature_note"]:
                return reply("記事功能關著喔。")
            self.store.add_note(msg.author.id, it.category, it.text, ts)
            return reply(self.phrase(s, "note_phrases", rng))

        if it.kind == "wish":
            if not s["feature_note"]:
                return reply("記事功能關著喔。")
            self._save_wishes(msg.author.id, it.wishes, now)
            return reply(self._wish_ack(it.wishes, rng))

        if it.kind == "suggest":
            return reply(self.suggest(msg.author.id, it.category, now, s, rng))

        if it.kind == "list":
            return reply(self.list_text(msg.author.id, it.scope, it.category))

        if it.kind == "cancel":
            ok = []
            for rid in it.ids:
                rem = self.store.get_reminder(rid)
                if rem and rem["status"] not in ("done", "cancelled", "expired") and can_touch(rem, msg.author):
                    ok.append(rid)
            if not ok:
                return reply(self.phrase(s, "not_found_phrases", rng))
            return self._cancel_now(msg, "reminders", ok, "", rng, s)

        if it.kind == "delete_note":
            ok = [i for i in it.ids if (n := self.store.get_note(i)) and n["user_id"] == msg.author.id]
            if not ok:
                return reply(self.phrase(s, "not_found_phrases", rng))
            return self._cancel_now(msg, "notes", ok, "", rng, s)

        if it.kind == "cancel_match":
            return self._cancel_match(msg, it, now, s, rng)

        if it.kind == "snooze":
            return self._snooze_by_text(msg, it, s, now, rng)

        if it.kind == "payday_set":
            if not 1 <= (it.day or 0) <= 31:
                return reply("發薪日要是 1～31 號喔。")
            self.store.set_meta(f"payday:{msg.author.id}", it.day)
            draft = self._draft_for(msg, now)
            if draft and draft["extra"].get("payday"):
                when = self._next_payday(msg.author.id, now, s)
                repeat = f"monthly:{it.day}" if draft["extra"].get("payday_repeat") else None
                return self._finish_draft(draft, when, repeat, "發薪日", True, msg.channel_id, msg.message_id,
                                          now, rng)
            return reply(self.phrase(s, "payday_set_phrases", rng, day=it.day))

        if it.kind == "weather":
            if not s["feature_weather"]:
                return reply("天氣功能關著喔。")
            county, town = (it.county, it.town) if it.county else self.location(msg.author.id, s)
            return [NeedWeather(county, msg.channel_id, reply_to=msg.message_id, town=town, mode=it.mode,
                                rain_q=it.rain)]

        if it.kind == "location":
            self.store.set_meta(f"loc:{msg.author.id}", {"county": it.county, "town": it.town})
            return reply(self.phrase(s, "location_phrases", rng, place=it.county + it.town))

        return reply(self.phrase(s, "unknown_phrases", rng))

    # ---------- 提醒 ----------
    def _targets(self, msg: Incoming, s: dict) -> tuple[list[str], bool]:
        targets = []
        if s["feature_mention_others"]:
            targets = [f"u:{u}" for u in msg.mention_user_ids] + [f"r:{r}" for r in msg.mention_role_ids]
        return (targets, True) if targets else ([f"u:{msg.author.id}"], False)

    def _reminder(self, msg: Incoming, it: Intent, s: dict, now: datetime, rng) -> list:
        ts = int(now.timestamp())
        targets, others = self._targets(msg, s)
        extra = {k: v for k, v in (("verb", it.verb), ("max_count", it.max_count),
                                   ("until", int(it.until.timestamp()) if it.until else None),
                                   ("offset", it.offset), ("event", it.event), ("topic", it.topic)) if v}
        home = self._home(msg)
        where = self._where(msg.channel_id, home)
        base = dict(channel_id=home, creator_id=msg.author.id, targets=targets, text=it.text, now=ts)

        if it.rain:
            rid = self.store.add_reminder(kind="rain", due_at=FAR_FUTURE, extra=extra, **base)
            return self._reply(msg, self.phrase(s, "rain_confirm_phrases", rng) + where, confirm_buttons(rid), rid)

        if it.payday:
            extra.update(payday=True, payday_repeat=bool(it.repeat))
            when = self._next_payday(msg.author.id, now, s)
            if when is None:
                rid = self.store.add_reminder(due_at=0, status="draft", extra=extra, **base)
                return self._reply(msg, self.phrase(s, "payday_ask_phrases", rng), reminder_id=rid)
            it.when, it.repeat, it.phrase = when, (f"monthly:{when.day}" if it.repeat else None), "發薪日"

        if it.ask:
            rid = self.store.add_reminder(due_at=0, status="draft", extra=extra, **base)
            return self._ask(msg, it, s, now, rng, rid)

        if it.max_count and not it.repeat:
            extra["nag_max"] = it.max_count - 1        # 「提醒我三次」= 響一次 + 再提醒兩次
        rid = self.store.add_reminder(due_at=int(it.when.timestamp()), repeat=it.repeat, extra=extra, **base)
        line = self._confirm_line(it.when, it.repeat, it.phrase, it.dated, targets if others else None,
                                  it.topic, s, now, rng)
        return self._reply(msg, line + where, confirm_buttons(rid), rid)

    def _confirm_line(self, when, repeat, phrase, dated, others, topic, s, now, rng) -> str:
        """設好提醒的回覆：短、不重述內容。常見提醒用它自己的說法；時間講得很短時偶爾照著講。"""
        if others:
            line = self.phrase(s, "confirm_other_phrases", rng, who=" ".join(map(mention_str, others)))
        elif (t := self._topic(topic, s)) and t.confirm:
            line = self.pick(f"topic:{t.name}", t.confirm, rng)
        elif repeat:
            line = self.phrase(s, "repeat_phrases", rng)
        elif phrase and len(phrase) <= SHORT_WHEN and not re.search(r"\d", phrase) and rng.random() < 0.5:
            line = self.phrase(s, "confirm_when_phrases", rng, when=self._echo(phrase, when, dated, now))
        else:
            line = self.phrase(s, "confirm_phrases", rng)
        if s["show_exact_time"] and when:
            line += f"\n-# {timeparse.fmt_dt(when, self.tz)}"
        return line

    def _echo(self, phrase: str, when: datetime, dated: bool, now: datetime) -> str:
        """照使用者的話講時間；沒講哪天、結果又不是今天，就補上「明天」之類。"""
        if not phrase:
            return timeparse.fmt_dt(when, self.tz)
        if not dated:
            diff = (when.astimezone(self.tz).date() - now.astimezone(self.tz).date()).days
            if diff:
                local = when.astimezone(self.tz)
                phrase = {1: "明天", 2: "後天"}.get(diff, f"{local.month}/{local.day} ") + phrase
        return phrase

    def _ask(self, msg: Incoming, it: Intent, s: dict, now, rng, rid: int) -> list:
        if it.ask == "event":
            q = self.phrase(s, "ask_event_phrases", rng, event=it.event)
        elif it.ask == "vague":
            q = self.phrase(s, "ask_vague_phrases", rng, vague=it.event)
        else:
            q = self.phrase(s, "ask_phrases", rng)
        return self._reply(msg, q, self._pick_buttons("pick", rid, now, s), reminder_id=rid)

    def _pick_buttons(self, action: str, rid: int, now: datetime, s: dict) -> list[Button]:
        return [Button(f"rem:{action}:{rid}:{code}", label) for code, label in PICKS
                if self._choice_dt(code, now, s) is not None]

    def _choice_dt(self, code: str, now: datetime, s: dict) -> datetime | None:
        local = now.astimezone(self.tz)
        at = lambda d, key: datetime.combine(d, time(*map(int, s[key].split(":"))), self.tz)  # noqa: E731
        tomorrow = local.date() + timedelta(days=1)
        if code.isdigit():
            return now + timedelta(minutes=int(code))
        if code == "tmr":
            return at(tomorrow, "btn_tomorrow_time")
        if code == "tonight":
            t = at(local.date(), "time_night")
            return t if t > now else None
        if code == "tmrm":
            return at(tomorrow, "time_morning")
        if code == "tmra":
            return at(tomorrow, "time_afternoon")
        if code == "wknd":
            d = local.date() + timedelta(days=(5 - local.weekday()) % 7)
            t = at(d, "time_morning")
            return t if t > now else at(d + timedelta(days=7), "time_morning")
        return None

    def _draft_for(self, msg: Incoming, now: datetime) -> dict | None:
        """這個人最近一個還在等時間的提醒（回覆那則問句，或 30 分鐘內）。"""
        cutoff = int(now.timestamp()) - DRAFT_MINUTES * 60
        drafts = [r for r in self.store.all_reminders(("draft",)) if r["creator_id"] == msg.author.id]
        if msg.reply_to_bot_message_id:
            hit = [r for r in drafts if r["message_id"] == msg.reply_to_bot_message_id]
            if hit:
                return hit[0]
        fresh = [r for r in drafts if r["created_at"] >= cutoff]
        return max(fresh, key=lambda r: r["id"]) if fresh else None

    def _finish_draft(self, draft: dict, when: datetime, repeat, phrase: str, dated: bool, channel_id: int,
                      reply_to: int | None, now: datetime, rng) -> list:
        s = self.store.settings()
        ex = draft["extra"]
        when = when + timedelta(seconds=ex.get("offset", 0))
        fields = dict(status="pending", due_at=int(when.timestamp()), repeat=repeat)
        if ex.get("max_count") and not repeat:
            ex["nag_max"] = ex["max_count"] - 1
            fields["extra"] = ex
        self.store.update_reminder(draft["id"], **fields)
        others = draft["targets"] if draft["targets"] != [f"u:{draft['creator_id']}"] else None
        line = self._confirm_line(when, repeat, "" if ex.get("offset") else phrase, dated, others,
                                  ex.get("topic", ""), s, now, rng)
        out = []
        if draft["message_id"]:
            out.append(Edit(self._ch(draft["message_id"], channel_id), draft["message_id"]))
        line += self._where(channel_id, draft["channel_id"])
        out.append(Send(channel_id, line, confirm_buttons(draft["id"]), reply_to=reply_to, reminder_id=draft["id"]))
        return out

    def _next_payday(self, user_id: int, now: datetime, s: dict) -> datetime | None:
        day = self.store.get_meta(f"payday:{user_id}") or s.get("payday") or 0
        if not day:
            return None
        h, m = map(int, s["date_only_time"].split(":"))
        return timeparse.next_occurrence(f"monthly:{int(day)}", now, time(h, m), self.tz)

    # ---------- 延後（用說的） ----------
    def _snooze_by_text(self, msg: Incoming, it: Intent, s: dict, now: datetime, rng) -> list:
        rem = None
        if msg.reply_to_bot_message_id:
            rem = self.store.reminder_by_message(msg.reply_to_bot_message_id)
        if rem is None:
            fired = [r for r in self.store.all_reminders(("fired",)) if can_touch(r, msg.author)]
            rem = max(fired, key=lambda r: r["fired_at"] or 0) if fired else None
        if rem is None:
            return self._reply(msg, self.phrase(s, "no_target_phrases", rng))
        if not can_touch(rem, msg.author):
            return self._reply(msg, "這不是你的提醒喔。")
        if it.ask:
            q = self.phrase(s, "ask_event_phrases", rng, event=it.event) if it.ask == "event" else \
                self.phrase(s, "ask_phrases", rng)
            return self._reply(msg, q, self._pick_buttons("snz", rem["id"], now, s))
        return self._apply_snooze(rem, it.when, self._echo(it.phrase, it.when, it.dated, now), msg.channel_id,
                                  msg.message_id, s, rng)

    def _apply_snooze(self, rem: dict, when: datetime, echo: str, channel_id: int, reply_to, s, rng) -> list:
        self.store.update_reminder(rem["id"], status="pending", due_at=int(when.timestamp()), nag_count=0)
        out = []
        if rem["message_id"]:
            out.append(Edit(self._ch(rem["message_id"], rem["channel_id"]), rem["message_id"],
                            append=f"-# 💤 {timeparse.fmt_dt(when, self.tz)}"))
        if echo and len(echo) <= SHORT_WHEN + 1 and not re.search(r"\d", echo):
            line = self.phrase(s, "snooze_when_phrases", rng, when=echo)
        else:
            line = self.phrase(s, "snooze_phrases", rng)
        out.append(Send(channel_id, line, reply_to=reply_to))
        return out

    # ---------- 取消／刪除：直接做，附「復原」 ----------
    def _cancel_match(self, msg: Incoming, it: Intent, now: datetime, s: dict, rng) -> list:
        uid = msg.author.id
        if it.scope == "notes":
            notes = self.store.user_notes(uid)
            hits = [n["id"] for n in notes if (it.all and not it.keyword) or fuzzy_match(it.keyword, n["text"])]
            if not hits:
                return self._reply(msg, self.phrase(s, "not_found_phrases", rng))
            return self._cancel_now(msg, "notes", hits, "", rng, s, many=it.all)
        rems = self.store.user_reminders(uid)
        if it.day is not None:
            day = now.astimezone(self.tz).date() + timedelta(days=it.day)
            rems = [r for r in rems if datetime.fromtimestamp(r["due_at"], self.tz).date() == day]
        if it.keyword:
            rems = [r for r in rems if fuzzy_match(it.keyword, r["text"])]
        elif not it.all and it.day is None:
            return self._reply(msg, "要取消哪一個？\n" + self.list_text(uid, "reminders", None))
        if not rems:
            return self._reply(msg, self.phrase(s, "not_found_phrases", rng))
        scope = {0: "今天", 1: "明天"}.get(it.day, "")
        return self._cancel_now(msg, "reminders", [r["id"] for r in rems], scope, rng, s,
                                many=it.all or it.day is not None)

    def _cancel_now(self, msg: Incoming, kind: str, ids: list[int], scope: str, rng, s: dict, many=False) -> list:
        backup = []
        for i in ids:
            if kind == "notes":
                n = self.store.get_note(i)
                if n and self.store.delete_note(i, msg.author.id):
                    backup.append(n)
            else:
                rem = self.store.get_reminder(i)
                backup.append({"id": i, "status": rem["status"]})
                self.store.update_reminder(i, status="cancelled")
        token = int(self.store.get_meta("undo_seq", 0)) + 1
        self.store.set_meta("undo_seq", token)
        self.store.set_meta(f"undo:{token}", {"user": msg.author.id, "kind": kind, "items": backup})
        if many:
            line = self.phrase(s, "cancel_all_phrases", rng, scope=scope).replace("好，的", "好，")
        else:
            line = self.phrase(s, "cancel_phrases", rng)
        return self._reply(msg, line, [Button(f"rem:undo:{token}", "復原")])

    def _when_label(self, r: dict) -> str:
        if r["kind"] == "rain":
            return "下雨天"
        rep = timeparse.describe_repeat(r["repeat"])
        return timeparse.fmt_dt(datetime.fromtimestamp(r["due_at"], self.tz), self.tz) + (f" {rep}" if rep else "")

    def list_text(self, user_id: int, scope: str, category: str | None) -> str:
        out = []
        if scope in ("reminders", "all"):
            rems = self.store.user_reminders(user_id)
            out.append("📋 **接下來的提醒**" if rems else "目前沒有提醒。")
            for r in rems[:30]:
                bits = [f"`#{r['id']}` {self._when_label(r)}・{r['text'] or '提醒'}"]
                if r["status"] == "fired":
                    bits.append("🔔")
                if r["targets"] != [f"u:{user_id}"]:
                    bits.append("→ " + " ".join(map(mention_str, r["targets"])))
                out.append(" ".join(bits))
        if scope in ("notes", "all"):
            notes = self.store.user_notes(user_id, category)
            title = f"📝 **{category}**" if category else "📝 **記事**"
            out.append(title if notes else f"{title}：還沒有東西。")
            last = None
            for n in notes[:60]:
                if not category and n["category"] != last:
                    out.append(f"__{n['category']}__")
                    last = n["category"]
                out.append(f"`#{n['id']}` {n['text']}")
        return "\n".join(out)[:2000]

    def view_text(self, rem: dict, now: datetime, user_id: int) -> str:
        """「查看」按鈕：這個提醒接下來的時間，再列其他提醒。"""
        lines = []
        if rem["status"] not in ("pending", "fired"):
            lines.append(f"`#{rem['id']}` 已經結束了")
        elif rem["kind"] == "rain":
            lines.append(f"`#{rem['id']}` 下雨天・{rem['text']}")
        elif rem["repeat"]:
            due = datetime.fromtimestamp(rem["due_at"], self.tz)
            times, t, until = [due], due, rem["extra"].get("until")
            count_left = (rem["extra"].get("max_count") or 99) - rem["extra"].get("count", 0)
            while len(times) < min(5, count_left):
                t = timeparse.next_occurrence(rem["repeat"], t, due.time(), self.tz)
                if until and t.timestamp() > until:
                    break
                times.append(t)
            lines.append(f"`#{rem['id']}` {rem['text'] or '提醒'}・{timeparse.describe_repeat(rem['repeat'])}")
            lines += [f"・{timeparse.fmt_dt(x, self.tz)}" for x in times]
        else:
            lines.append(f"`#{rem['id']}` {self._when_label(rem)}・{rem['text'] or '提醒'}")
        others = [r for r in self.store.user_reminders(user_id) if r["id"] != rem["id"]]
        if others:
            lines.append("\n**其他提醒**")
            lines += [f"`#{r['id']}` {self._when_label(r)}・{r['text'] or '提醒'}" for r in others[:15]]
        return "\n".join(lines)

    # ---------- 按鈕 ----------
    def press(self, custom_id: str, user: User, now: datetime, channel_id: int | None = None) -> PressResult:
        m = re.fullmatch(r"rem:(done|snooze|cancel|pick|snz|undo|stop|view):(\d+)(?::([0-9a-z]+))?", custom_id)
        if not m:
            return PressResult(ephemeral="這個按鈕失效了。")
        action, rid, arg = m.group(1), int(m.group(2)), m.group(3)
        s = self.store.settings()
        rng = random.Random(rid * 7919 + int(now.timestamp()))

        if action == "undo":
            job = self.store.get_meta(f"undo:{rid}")
            if not job:
                return PressResult(ephemeral="已經沒辦法復原了。")
            if job["user"] != user.id:
                return PressResult(ephemeral="這不是你的喔。")
            self.store.set_meta(f"undo:{rid}", None)
            for item in job["items"]:
                if job["kind"] == "notes":
                    nid = self.store.add_note(item["user_id"], item["category"], item["text"], item["created_at"])
                    self.store.update_note(nid, mentioned_at=item.get("mentioned_at") or item["created_at"],
                                           mentions=item.get("mentions") or 1)
                else:
                    self.store.update_reminder(item["id"], status=item["status"])
            return PressResult(append="-# ↩️ 復原了")

        rem = self.store.get_reminder(rid)
        if not rem:
            return PressResult(ephemeral="找不到這個提醒。")
        if not can_touch(rem, user):
            return PressResult(ephemeral="這不是你的提醒喔。")

        if action == "view":
            return PressResult(ephemeral=self.view_text(rem, now, user.id))
        if action == "pick":
            if rem["status"] != "draft":
                return PressResult(ephemeral="已經設定好了。")
            when = self._choice_dt(arg or "", now, s)
            acts = self._finish_draft(rem, when, None, dict(PICKS).get(arg, ""), True, channel_id or rem["channel_id"], None,
                                      now, rng)
            return PressResult(append=f"-# {dict(PICKS).get(arg, '')}", actions=[a for a in acts if isinstance(a, Send)])
        if action == "snz":
            when = self._choice_dt(arg or "", now, s)
            acts = self._apply_snooze(rem, when, dict(PICKS).get(arg, ""), channel_id or rem["channel_id"], None, s, rng)
            return PressResult(append=f"-# {dict(PICKS).get(arg, '')}", actions=acts)
        if action == "done":
            self.store.update_reminder(rid, status="done")
            return PressResult(append=f"-# ✅ {user.name or ''}".rstrip())
        if action == "stop":
            self.store.update_reminder(rid, status="done")
            if rem["parent_id"]:
                self.store.update_reminder(rem["parent_id"], status="cancelled")
            return PressResult(append="-# 之後都不提醒了")
        if action == "cancel":
            if rem["status"] in ("done", "cancelled"):
                return PressResult(ephemeral="這個提醒已經結束了。")
            self.store.update_reminder(rid, status="cancelled")
            if rem["status"] == "draft":
                return PressResult(append="-# 好，先不設")
            return PressResult(append=f"-# {self.phrase(s, 'cancel_phrases', rng)}", strike=True)
        when = self._choice_dt(arg or str(s["btn_later_minutes"]), now, s)
        self.store.update_reminder(rid, status="pending", due_at=int(when.timestamp()), nag_count=0)
        return PressResult(append=f"-# 💤 {timeparse.fmt_dt(when, self.tz)}")

    # ---------- 時間到 ----------
    def _fire_text(self, rem: dict, s: dict, rng, nag: bool = False) -> str:
        mentions = " ".join(map(mention_str, rem["targets"]))
        if rem["kind"] == "timer":
            return fill(s["timer_template"], mentions=mentions, text=rem["text"],
                        duration=rem["extra"].get("duration", ""))
        t = self._topic(rem["extra"].get("topic", ""), s)
        if t and t.fire:
            body = self.pick(f"fire:{t.name}", t.fire, rng)
        else:
            body = self.phrase(s, "nag_phrases" if nag else "fire_phrases", rng, text=rem["text"] or "這件事")
        return f"{mentions} {body}"

    def tick(self, now: datetime, default_channel_id: int | None = None) -> list:
        s = self.store.settings()
        ts = int(now.timestamp())
        rng = random.Random(ts)
        actions: list = []

        for rem in self.store.due_reminders(ts):
            content = self._fire_text(rem, s, rng)
            if rem["repeat"]:
                actions += self._expire_previous(rem)
                occ = self._occurrence(rem, ts)
                ex = dict(rem["extra"])
                ex["count"] = ex.get("count", 0) + 1
                due = datetime.fromtimestamp(rem["due_at"], self.tz)
                nxt = timeparse.next_occurrence(rem["repeat"], max(now, due), due.time(), self.tz)
                finished = (ex.get("max_count") and ex["count"] >= ex["max_count"]) or \
                           (ex.get("until") and nxt.timestamp() > ex["until"])
                if finished:
                    self.store.update_reminder(rem["id"], status="done", extra=ex)
                else:
                    self.store.update_reminder(rem["id"], due_at=int(nxt.timestamp()), extra=ex)
                actions.append(Send(rem["channel_id"], content[:2000], fired_buttons(occ, s, repeat=not finished),
                                    ping=True, reminder_id=occ))
            else:
                self.store.update_reminder(rem["id"], status="fired", fired_at=ts, nag_count=0)
                actions.append(Send(rem["channel_id"], content[:2000], fired_buttons(rem["id"], s),
                                    ping=True, reminder_id=rem["id"]))

        before = ts - int(s["nag_interval_minutes"]) * 60
        for rem in self.store.nag_candidates(before, 99):
            limit = rem["extra"].get("nag_max", s["nag_max"] if s["nag_enabled"] else 0)
            parent = self.store.get_reminder(rem["parent_id"]) if rem["parent_id"] else None
            if rem["nag_count"] >= int(limit) or (parent and (parent["repeat"] or "").startswith("interval:")):
                continue    # 每幾小時那種本來就很密，不用再催
            if rem["message_id"]:
                actions.append(Edit(self._ch(rem["message_id"], rem["channel_id"]), rem["message_id"], clear_buttons=True))
            self.store.update_reminder(rem["id"], fired_at=ts, nag_count=rem["nag_count"] + 1)
            actions.append(Send(rem["channel_id"], self._fire_text(rem, s, rng, nag=True)[:2000],
                                fired_buttons(rem["id"], s, repeat=bool(parent and parent["status"] == "pending")),
                                ping=True, reminder_id=rem["id"]))

        if s["feature_weather"]:
            county, town = self.location(self.owner_id, s)
            has_rain = any(r["kind"] == "rain" for r in self.store.all_reminders(("pending",)))
            local = now.astimezone(self.tz)
            h, m = map(int, s["weather_daily_time"].split(":"))
            target = datetime.combine(local.date(), time(h, m), self.tz)
            if (s["weather_daily_enabled"] or has_rain) and target <= local < target + timedelta(minutes=30) \
                    and self.store.get_meta("weather_sent") != local.date().isoformat():
                self.store.set_meta("weather_sent", local.date().isoformat())
                actions.append(NeedWeather(county, default_channel_id, purpose="daily", town=town, mode="today",
                                           post=bool(s["weather_daily_enabled"]), rain_check=has_rain))
            last = self.store.get_meta("weather_checked", 0) or 0
            if s["weather_alert_enabled"] and ts - last >= int(s["weather_check_minutes"]) * 60:
                self.store.set_meta("weather_checked", ts)
                actions.append(NeedWeather(county, default_channel_id, purpose="monitor", town=town))
        return actions

    # ---------- 天氣 ----------
    def location(self, user_id: int | None, s: dict) -> tuple[str, str]:
        """這個人說過自己在哪；沒說過就用設定的縣市。"""
        loc = self.store.get_meta(f"loc:{user_id}") if user_id else None
        return (loc["county"], loc.get("town", "")) if loc else (s["weather_county"], "")

    def weather_result(self, nw: NeedWeather, b, now: datetime) -> list:
        """轉接層抓到天氣（weather.Bundle）之後呼叫。"""
        s = self.store.settings()
        rng = random.Random(int(now.timestamp()))
        channel = nw.channel_id or self.home_channel_id
        if nw.purpose == "monitor":
            return self._weather_monitor(b, now, s, rng, channel)
        out = []
        if nw.purpose == "daily":
            if nw.post:
                out.append(Send(channel, "", embed=self.day_card(b, now.astimezone(self.tz).date(), now, s, rng)))
            if nw.rain_check:
                out += self.rain_check(b, now)
            return out
        if nw.need_week and b.week:
            return [Send(channel, "", reply_to=nw.reply_to, ephemeral=nw.ephemeral,
                         embed=self.week_card(b, nw.mode == "weekend", now, s, rng))]
        day = self._card_day(nw.mode, now, s)
        text = self._rain_answer(b, day, now, s, rng) if nw.rain_q else ""
        return [Send(channel, text, reply_to=nw.reply_to, ephemeral=nw.ephemeral,
                     embed=self.day_card(b, day, now, s, rng))]

    def weather_wait(self, nw: NeedWeather) -> list:
        """第一輪重試都失敗、要等幾分鐘時：有人問的才先說一聲，自動的不吵。"""
        if nw.purpose != "show":
            return []
        s = self.store.settings()
        line = self.phrase(s, "weather_wait_phrases", random.Random(0), minutes=s["weather_retry_wait"])
        return [Send(nw.channel_id or self.home_channel_id, line, reply_to=nw.reply_to)] if line else []

    def weather_error(self, nw: NeedWeather, err: str) -> list:
        if nw.purpose != "show":
            return []
        return [Send(nw.channel_id or self.home_channel_id, f"🌧️ 天氣查不到：{err}", reply_to=nw.reply_to,
                     ephemeral=nw.ephemeral)]

    def _card_day(self, mode: str, now: datetime, s: dict):
        local = now.astimezone(self.tz)
        rule = mode if mode in ("today", "tomorrow") else s["weather_slot_day"]
        if rule == "auto":
            first = min(int(h) for h in str(s["weather_slot_hours"]).split(","))
            rule = "today" if local.hour < first else "tomorrow"
        return local.date() + timedelta(days=1 if rule == "tomorrow" else 0)

    def _day_label(self, day, now: datetime) -> str:
        diff = (day - now.astimezone(self.tz).date()).days
        return {0: "今天", 1: "明天", 2: "後天"}.get(diff, f"{day.month}/{day.day}")

    def _advice(self, slots: list, s: dict, rng, day: str = "今天") -> tuple[str, int]:
        """一句叮嚀＋卡片顏色。"""
        if any(weather.is_rain(x, int(s["weather_rain_threshold"])) for x in slots):
            return self.phrase(s, "rain_advice", rng, day=day), 0x4A90D9
        temps = [v for x in slots for v in (x.temp, x.max_t, x.min_t) if v is not None]
        if temps and max(temps) >= int(s["weather_hot"]):
            return self.phrase(s, "hot_advice", rng, day=day), 0xE8833A
        if temps and min(temps) <= int(s["weather_cold"]):
            return self.phrase(s, "cold_advice", rng, day=day), 0x4FB3BF
        return self.phrase(s, "nice_advice", rng, day=day), 0x5CB85C

    def day_card(self, b, day, now: datetime, s: dict, rng) -> dict:
        """現在天氣＋某一天四個時段＋整天溫度範圍＋一句叮嚀。"""
        hours = [int(h) for h in str(s["weather_slot_hours"]).split(",") if h.strip().isdigit()]
        fields, used = [], []
        for h in hours:
            at = datetime.combine(day, time(h, 0), self.tz)
            x = weather.slot_at(b.slots, at)
            if x:
                used.append(x)
                temp = f"{x.temp:.0f}°C" if x.temp is not None else ""
                pop = f" ☔{x.pop}%" if x.pop is not None else ""
                fields.append({"name": f"{h:02d}:00 {weather.icon(x.wx)}", "value": f"{x.wx or '—'}\n{temp}{pop}",
                               "inline": True})
            else:
                fields.append({"name": f"{h:02d}:00", "value": "—", "inline": True})
        day_slots = [x for x in b.slots if x.start.date() == day] or used
        advice, color = self._advice(day_slots, s, rng, self._day_label(day, now))
        rng_t = weather.day_range(b.slots, day)
        span = f" {rng_t[0]:.0f}–{rng_t[1]:.0f}°C" if rng_t else ""
        fields.append({"name": f"{self._day_label(day, now)}{span}", "value": advice, "inline": False})
        cur = b.current
        now_line = ""
        if cur and (cur.wx or cur.temp is not None):
            now_line = "現在 **" + " ".join(p for p in (cur.wx, f"{cur.temp:.0f}°C" if cur.temp is not None else "") if p) + "**"
        head = cur.wx if cur and cur.wx else (used[0].wx if used else "")
        return {"title": f"{weather.icon(head)} {b.place}", "description": now_line, "fields": fields,
                "color": color, "footer": "中央氣象署"}

    def week_card(self, b, weekend: bool, now: datetime, s: dict, rng=None) -> dict:
        days = weather.week_days(b.week)
        if weekend:
            days = [d for d in days if d[0].weekday() >= 5][:2] or days
        names = "一二三四五六日"
        fields = [{"name": f"週{names[d.weekday()]} {d.month}/{d.day} {weather.icon(wx)}",
                   "value": f"{wx or '—'}\n" + (f"{lo:.0f}–{hi:.0f}°C" if lo is not None else "")
                            + (f" ☔{pop}%" if pop is not None else ""), "inline": True}
                  for d, wx, lo, hi, pop in days[:7]]
        shown = {d for d, *_ in days[:7]}
        advice, color = self._advice([x for x in b.week if x.start.date() in shown], s, rng or random.Random(0),
                                     "週末" if weekend else "這幾天")
        return {"title": f"📅 {b.place}{' 週末' if weekend else ' 一週'}", "description": advice, "fields": fields,
                "color": color, "footer": "中央氣象署"}

    def _rain_answer(self, b, day, now: datetime, s: dict, rng) -> str:
        """「明天會下雨嗎」：直接回答，下面再附卡片。"""
        label = self._day_label(day, now)
        th = int(s["weather_rain_threshold"])
        rainy = [x for x in b.slots if x.start.date() == day and x.end > now and weather.is_rain(x, th)]
        if not rainy:
            return self.phrase(s, "rain_no_phrases", rng, when=label)
        first = rainy[0]
        return self.phrase(s, "rain_yes_phrases", rng, when=f"{label}{hour_label(first.start.hour)}點後",
                           pop=max(x.pop or 0 for x in rainy))

    def _quiet(self, now: datetime, s: dict) -> bool:
        q = str(s.get("weather_quiet", "")).strip()
        if not q:
            return False
        a, b = [time(*map(int, x.split(":"))) for x in q.split("-")]
        t = now.astimezone(self.tz).time()
        return (a <= t or t < b) if a > b else (a <= t < b)

    def _weather_monitor(self, b, now: datetime, s: dict, rng, channel: int) -> list:
        """跟上次記下的預報比：未來 N 小時內，有時段從「不會下雨」變成「可能下雨」或出現雷雨等狀況，就通知。"""
        if self._quiet(now, s):
            return []           # 安靜時段先不比（也不更新紀錄），等結束後再一起比
        th = int(s["weather_rain_threshold"])
        snap = self.store.get_meta("wx_snap") or {}
        alerted = set(self.store.get_meta("wx_alerted") or [])
        horizon = now + timedelta(hours=int(s["weather_alert_hours"]))
        changed = []
        for x in b.slots:
            key = x.start.isoformat()
            rain, sp = weather.is_rain(x, th), weather.special(x)
            old = snap.get(key)
            if old is not None and x.end > now and x.start < horizon and key not in alerted:
                if (rain and not old[0]) or (sp and sp != old[1]):
                    changed.append((x, sp))
            snap[key] = [rain, sp]
        cutoff = (now - timedelta(days=1)).isoformat()
        self.store.set_meta("wx_snap", {k: v for k, v in snap.items() if k >= cutoff})
        if not changed:
            return []
        alerted |= {x.start.isoformat() for x, _ in changed}
        self.store.set_meta("wx_alerted", sorted(k for k in alerted if k >= cutoff))
        first, sp = changed[0]
        what = f"有{sp}" if sp else "下雨"
        line = self.phrase(s, "alert_phrases", rng, hour=hour_label(first.start.astimezone(self.tz).hour), what=what)
        fields = [{"name": f"{x.start.astimezone(self.tz):%H:%M} {weather.icon(x.wx)}",
                   "value": f"{x.wx or '—'}" + (f" ☔{x.pop}%" if x.pop is not None else ""), "inline": True}
                  for x, _ in changed[:6]]
        owner = f"<@{self.owner_id}> " if self.owner_id else ""
        return [Send(channel, owner.strip(), ping=True, embed={
            "title": f"⚠️ 天氣變化・{b.place}", "description": line, "fields": fields, "color": 0xE0A100,
            "footer": "中央氣象署"})]

    def weather_text(self, b, now: datetime) -> str:
        """卡片的純文字版（沒有卡片可以顯示時用）。"""
        card = self.day_card(b, self._card_day("", now, self.store.settings()), now, self.store.settings(),
                             random.Random(0))
        return embed_text(card)

    def rain_check(self, b, now: datetime) -> list:
        """每天早上看完預報後呼叫：今天會下雨就把「下雨天提醒」送出去。"""
        s = self.store.settings()
        today = now.astimezone(self.tz).date()
        th = int(s["weather_rain_threshold"])
        rainy = [x for x in b.slots if x.start.date() == today and weather.is_rain(x, th)]
        if not rainy:
            return []
        pop = max(x.pop or 0 for x in rainy)
        ts, actions = int(now.timestamp()), []
        for rem in self.store.all_reminders(("pending",)):
            if rem["kind"] != "rain":
                continue
            actions += self._expire_previous(rem)
            occ = self._occurrence(rem, ts)
            content = fill(s["rain_template"], mentions=" ".join(map(mention_str, rem["targets"])),
                           text=rem["text"] or "帶把傘", pop=pop)
            actions.append(Send(rem["channel_id"], content[:2000], fired_buttons(occ, s, repeat=True), ping=True,
                                reminder_id=occ))
        return actions

    def _occurrence(self, rem: dict, ts: int) -> int:
        """重複提醒／下雨天提醒：這一次另外存一筆（按鈕、延後都對這筆）。"""
        occ = self.store.add_reminder(
            channel_id=rem["channel_id"], creator_id=rem["creator_id"], targets=rem["targets"], text=rem["text"],
            due_at=ts if rem["kind"] == "rain" else rem["due_at"], parent_id=rem["id"], status="fired", now=ts,
            extra={k: rem["extra"][k] for k in ("verb", "topic") if k in rem["extra"]})
        self.store.update_reminder(occ, fired_at=ts)
        return occ

    def _expire_previous(self, rem: dict) -> list:
        """上一次還沒按「好」的就算過期，不要跟這次一起再提醒。"""
        out = []
        for old in self.store.all_reminders(("fired",)):
            if old["parent_id"] == rem["id"]:
                self.store.update_reminder(old["id"], status="expired")
                if old["message_id"]:
                    out.append(Edit(self._ch(old["message_id"], old["channel_id"]), old["message_id"]))
        return out

    # ---------- 願望清單 ----------
    def _save_wishes(self, user_id: int, wishes, now: datetime) -> list[int]:
        """存進清單；同一件事又提到就只更新「最後提到」，不重複存。"""
        ts, ids = int(now.timestamp()), []
        for w in wishes:
            same = next((n for n in self.store.user_notes(user_id, w.category)
                         if n["text"] == w.text or (len(w.text) >= 3 and fuzzy_match(w.text, n["text"])
                                                    and fuzzy_match(n["text"], w.text))), None)
            if same:
                self.store.update_note(same["id"], mentioned_at=ts, mentions=(same.get("mentions") or 1) + 1)
                ids.append(same["id"])
            else:
                ids.append(self.store.add_note(user_id, w.category, w.text, ts))
        return ids

    def _wish_ack(self, wishes, rng) -> str:
        s = self.store.settings()
        w = wishes[0]
        return self.pick(f"wish:{w.category}", wish_acks(s, w.category), rng, verb=w.verb)

    def suggest(self, user_id: int, category: str, now: datetime, s: dict, rng=None) -> str:
        """從清單挑幾個：加權輪流（smooth weighted round-robin）。

        每問一次，每一項都加上自己的權重；分數最高的幾項被挑中後扣分。
        權重 = 1 + 4 × 0.5^(距離最後提到的天數 / 半衰期)，所以越近期提到的越常出現，
        但舊的分數也一直在累積，遲早一定會輪到——清單裡每一項都會被提到。
        """
        rng = rng or random.Random(0)
        verb = wish_verb(category)
        items = self.store.user_notes(user_id, category)
        if not items:
            return rng.choice([f"還沒聽你說過想{verb}什麼耶。", "清單是空的😅"])
        k = max(1, int(s["suggest_count"]))
        half = max(1.0, float(s["suggest_half_life_days"]))
        ts = now.timestamp()
        weight = {n["id"]: 1 + 4 * 0.5 ** (max(0.0, ts - (n.get("mentioned_at") or n["created_at"])) / 86400 / half)
                  for n in items}
        total = sum(weight.values())
        credit = {n["id"]: float(n.get("credit") or 0) + weight[n["id"]] for n in items}
        ranked = sorted(items, key=lambda n: (-credit[n["id"]], -(n.get("mentioned_at") or 0)))
        picked = ranked[:k]
        for n in items:
            c = 0.0 if len(items) <= k else credit[n["id"]] - (total / k if n in picked else 0)
            self.store.update_note(n["id"], credit=max(-total, min(total, c)))
        head = self.phrase(s, "suggest_phrases", rng, verb=verb)
        return head + "\n" + "\n".join(f"・{n['text']}" for n in picked)

    def ago(self, n: dict, now: datetime) -> str:
        days = (now.astimezone(self.tz).date() -
                datetime.fromtimestamp(n.get("mentioned_at") or n["created_at"], self.tz).date()).days
        if days <= 0:
            return "今天提到"
        if days == 1:
            return "昨天提到"
        if days < 14:
            return f"{days} 天前提到"
        if days < 60:
            return f"{days // 7} 週前提到"
        return f"{days // 30} 個月前提到"

    def sent(self, reminder_id: int, message_id: int, channel_id: int | None = None):
        """Send 帶 reminder_id 的訊息送出後呼叫：記下訊息編號（延後、再提醒、回答問句要用），
        也記下「這則訊息是哪個提醒」，之後有人回覆這則訊息就知道在講哪個。"""
        self.store.update_reminder(reminder_id, message_id=message_id)
        self.store.set_meta(f"msg:{message_id}", reminder_id)
        if channel_id:
            self.store.set_meta(f"msgch:{message_id}", channel_id)

    def next_event_at(self, now: datetime) -> datetime | None:
        """測試網頁「跳到下一件事」用：下一個提醒或再提醒的時間。"""
        s = self.store.settings()
        times = [r["due_at"] for r in self.store.all_reminders(("pending",)) if r["kind"] != "rain"]
        iv = int(s["nag_interval_minutes"]) * 60
        for r in self.store.all_reminders(("fired",)):
            limit = r["extra"].get("nag_max", s["nag_max"] if s["nag_enabled"] else 0)
            if r["kind"] == "reminder" and r["nag_count"] < int(limit):
                times.append((r["fired_at"] or 0) + iv)
        future = [t for t in times if t > now.timestamp()]
        return datetime.fromtimestamp(min(future), self.tz) if future else None


def hour_label(h: int) -> str:
    """0 點講成「半夜12」，其他照講（後面會接「點」）。"""
    return "半夜12" if h == 0 else str(h)


def embed_text(card: dict) -> str:
    lines = [f"**{card['title']}**"] + ([card["description"]] if card.get("description") else [])
    lines += [f"{f['name']}｜{f['value'].replace(chr(10), ' ')}" for f in card.get("fields", [])]
    return "\n".join(lines)


def can_touch(rem: dict, user: User) -> bool:
    if rem["creator_id"] == user.id or f"u:{user.id}" in rem["targets"]:
        return True
    return any(t.startswith("r:") and int(t[2:]) in user.role_ids for t in rem["targets"])


def fired_buttons(rid: int, s: dict, repeat: bool = False) -> list[Button]:
    out = [Button(f"rem:done:{rid}", s["btn_done_label"], "", "success"),
           Button(f"rem:snooze:{rid}:{int(s['btn_later_minutes'])}", s["btn_later_label"]),
           Button(f"rem:snooze:{rid}:tmr", s["btn_tomorrow_label"])]
    if repeat:
        out.append(Button(f"rem:stop:{rid}", s["btn_stop_label"], "", "danger"))
    return out


def confirm_buttons(rid: int) -> list[Button]:
    return [Button(f"rem:view:{rid}", "查看"), Button(f"rem:cancel:{rid}", "取消")]


def cancel_button(rid: int) -> Button:
    return Button(f"rem:cancel:{rid}", "取消")
