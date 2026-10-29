"""助理的核心邏輯，不碰 Discord、不碰網路。

輸入：一則訊息、按下一個按鈕、或「現在幾點了」（tick）。
輸出：要做的動作清單（送訊息、改訊息、查天氣）。
正式的 Discord bot（bot.py）和測試網頁（sim/）都用同一份。
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from . import timeparse
from .router import REMINDER_RE, Intent, extract_wishes, fuzzy_match, route
from .settings import phrases, wish_verb

HELP = """**我會做這些事**（@我 再說一句話就好，順序隨意）
⏰ **提醒**：`明天下午3點提醒我交報告`、`這幾天提醒我訂車票`、`10分鐘後叫我關火`
🔁 **重複**：`每天早上8點提醒我吃藥`、`每週三和週五提醒我運動`、`每15分鐘提醒一次，共四次`
👥 **提醒別人**：`明天中午提醒 @某人 開會`
☔ **看情況**：`下雨天提醒我帶傘`、`發薪日提醒我存錢`
⏱️ **計時**：`計時 25 分鐘`
📝 **記住**：`記住 靈感：…`、`我想看 葬送的芙莉蓮`、`我想學投資`
📋 **查詢**：`我有哪些提醒`、`我想看什麼`
🗑️ **取消**：`不用再提醒我喝水了`、`刪掉跟報告有關的提醒`、`取消 12`
💤 **延後**：提醒響了按按鈕，或說 `後天再提醒我`、`晚兩個小時再提醒`
🌤️ **天氣**：`天氣`、`台中天氣`"""

DRAFT_MINUTES = 30          # 問了時間之後，多久內回答都算數
FAR_FUTURE = 4102444800     # 2100 年：下雨天提醒這種「看情況」的，平常不會到期
PICKS = [("tonight", "今晚"), ("tmrm", "明天早上"), ("tmra", "明天下午"), ("wknd", "這週末")]


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


@dataclass
class Send:
    channel_id: int
    text: str
    buttons: list[Button] = field(default_factory=list)
    ping: bool = False                          # True 才真的 @ 到人
    reply_to: int | None = None                 # 回覆哪則訊息
    reminder_id: int | None = None              # 送出後要記下 message_id（呼叫 core.sent）


@dataclass
class Edit:
    channel_id: int
    message_id: int
    append: str = ""                            # 在原訊息後面加一行
    strike: bool = False                        # 原訊息加刪除線
    clear_buttons: bool = True


@dataclass
class NeedWeather:
    county: str
    channel_id: int | None                      # None = 預設頻道（每日天氣）
    reply_to: int | None = None
    post: bool = True                           # 要不要把天氣貼出來
    rain_check: bool = False                    # 查完要呼叫 core.rain_check（下雨天提醒）


@dataclass
class PressResult:
    ephemeral: str | None = None                # 只給按的人看的訊息（不改原訊息）
    append: str = ""
    strike: bool = False
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
    def __init__(self, store, tz, owner_id: int | None = None):
        self.store = store
        self.tz = tz
        self.owner_id = owner_id                 # 沒設定 wish_listen_ids 時，只聽這個人的願望
        self.last_intent: Intent | None = None   # 測試網頁顯示「判斷結果」用

    def listens_to(self, user_id: int, s: dict) -> bool:
        ids = {int(x) for x in re.findall(r"\d+", str(s.get("wish_listen_ids", "")))}
        return user_id in (ids or ({self.owner_id} if self.owner_id else set()))

    # ---------- 收訊息 ----------
    def handle(self, msg: Incoming, now: datetime) -> list:
        s = self.store.settings()
        text = re.sub(r"<@[!&]?\d+>", " ", msg.text).strip()
        rng = random.Random(msg.message_id or int(now.timestamp()))

        if not msg.mentioned:
            # 沒 @ bot：只聽指定成員的「我想畫…」這類願望，其他一律不回
            self.last_intent = None
            if not (s["feature_note"] and self.listens_to(msg.author.id, s)):
                return []
            wishes = extract_wishes(text, s)
            if not wishes:
                return []
            self.last_intent = Intent("wish", wishes=wishes)
            self._save_wishes(msg.author.id, wishes, now)
            if not s["passive_ack"]:
                return []
            return self._reply(msg, self._wish_ack(wishes, s, rng))

        it = route(text, now, s)

        if msg.reply_to_bot_message_id and it.kind in ("reminder", "unknown") \
                and re.search(r"延後|晚點|等等|等一下|再提醒|再說|延到", text):
            it = Intent("snooze", when=it.when, phrase=it.phrase, dated=it.dated, ask=it.ask, event=it.event,
                        seconds=it.seconds)
        self.last_intent = it

        draft = self._draft_for(msg, now)
        if draft:
            if re.fullmatch(r"(算了|不用了|先不要|不要了|取消|沒事)[。！!]?", text):
                self.store.update_reminder(draft["id"], status="cancelled")
                return self._reply(msg, rng.choice(["好，那就先不設了。", "OK，那先不提醒囉。"]))
            # 只講時間（「明天晚上」「8點吧」）才算回答；又講了「提醒…」就是新的指令
            if it.kind == "reminder" and it.when and not it.text and not it.ask \
                    and not re.search(REMINDER_RE, text) and not it.limit_label:
                return self._finish_draft(draft, it.when, it.repeat, it.phrase, it.dated, msg.channel_id,
                                          msg.message_id, now, rng)
        return self._dispatch(msg, it, s, now, rng)

    def _reply(self, msg: Incoming, text: str, buttons=None, reminder_id=None) -> list:
        return [Send(msg.channel_id, text[:2000], buttons or [], reply_to=msg.message_id, reminder_id=reminder_id)]

    def _dispatch(self, msg: Incoming, it: Intent, s: dict, now: datetime, rng) -> list:
        reply = lambda text, buttons=None, rid=None: self._reply(msg, text, buttons, rid)  # noqa: E731
        ts = int(now.timestamp())

        if it.error:
            return reply(f"🤔 {it.error}")
        if it.kind == "help":
            return reply(HELP)

        if it.kind == "reminder":
            if not s["feature_reminder"]:
                return reply("提醒功能目前關閉中。")
            return self._reminder(msg, it, s, now, rng)

        if it.kind == "timer":
            if not s["feature_timer"]:
                return reply("計時功能目前關閉中。")
            due = now + timedelta(seconds=it.seconds)
            rid = self.store.add_reminder(
                kind="timer", channel_id=msg.channel_id, creator_id=msg.author.id,
                targets=[f"u:{msg.author.id}"], text=it.text, due_at=int(due.timestamp()), now=ts,
                extra={"duration": fmt_seconds(it.seconds)})
            label = f"「{it.text}」" if it.text else ""
            return reply(f"⏱️ 開始計時 {fmt_seconds(it.seconds)}{label}，"
                         f"{due.astimezone(self.tz):%H:%M:%S} 叫你。\n-# #{rid}", [cancel_button(rid)])

        if it.kind == "note":
            if not s["feature_note"]:
                return reply("記事功能目前關閉中。")
            nid = self.store.add_note(msg.author.id, it.category, it.text, ts)
            line = say(rng.choice(phrases(s, "note_phrases")), category=it.category, text=it.text)
            return reply(line + (f"\n-# 📝 #{nid}" if s["show_exact_time"] else ""))

        if it.kind == "list":
            return reply(self.list_text(msg.author.id, it.scope, it.category))

        if it.kind == "wish":
            if not s["feature_note"]:
                return reply("記事功能目前關閉中。")
            ids = self._save_wishes(msg.author.id, it.wishes, now)
            line = self._wish_ack(it.wishes, s, rng)
            return reply(line + ("\n-# 📝 " + "、".join(f"#{i}（{w.category}）" for i, w in zip(ids, it.wishes))
                                 if s["show_exact_time"] else ""))

        if it.kind == "suggest":
            return reply(self.suggest(msg.author.id, it.category, now, s, rng))

        if it.kind == "cancel":
            done, denied = [], []
            for rid in it.ids:
                rem = self.store.get_reminder(rid)
                if not rem or rem["status"] in ("done", "cancelled", "expired"):
                    denied.append(f"#{rid}（找不到）")
                elif not can_touch(rem, msg.author):
                    denied.append(f"#{rid}（不是你的）")
                else:
                    self.store.update_reminder(rid, status="cancelled")
                    done.append(f"#{rid}「{rem['text'] or '提醒'}」")
            out = (["🗑️ 已取消 " + "、".join(done)] if done else []) + \
                  (["⚠️ 沒辦法取消 " + "、".join(denied)] if denied else [])
            return reply("\n".join(out))

        if it.kind == "delete_note":
            ok = [i for i in it.ids if self.store.delete_note(i, msg.author.id)]
            bad = [i for i in it.ids if i not in ok]
            out = (["🗑️ 已刪除記事 " + "、".join(f"#{i}" for i in ok)] if ok else []) + \
                  (["⚠️ 找不到你的記事 " + "、".join(f"#{i}" for i in bad)] if bad else [])
            return reply("\n".join(out))

        if it.kind == "cancel_match":
            return self._cancel_match(msg, it, now)

        if it.kind == "snooze":
            return self._snooze_by_text(msg, it, s, now, rng)

        if it.kind == "payday_set":
            if not 1 <= (it.day or 0) <= 31:
                return reply("發薪日要是 1～31 號喔。")
            self.store.set_meta(f"payday:{msg.author.id}", it.day)
            out = reply(f"記住了，你的發薪日是每月 {it.day} 號。")
            draft = self._draft_for(msg, now)
            if draft and draft["extra"].get("payday"):
                when = self._next_payday(msg.author.id, now, s)
                repeat = f"monthly:{it.day}" if draft["extra"].get("payday_repeat") else None
                out += self._finish_draft(draft, when, repeat, "發薪日", True, msg.channel_id, msg.message_id, now, rng)
            return out

        if it.kind == "weather":
            if not s["feature_weather"]:
                return reply("天氣功能目前關閉中。")
            return [NeedWeather(it.county or s["weather_county"], msg.channel_id, reply_to=msg.message_id)]

        return reply(rng.choice([
            "🤔 我聽不太懂，可以換個說法嗎？例如「明天下午3點提醒我…」「記住…」，或說「說明」看我會什麼。",
            "嗯…這句我還不會😅 試試「這幾天提醒我…」「我想看…」，或說「說明」。"]))

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

    def _wish_ack(self, wishes, s: dict, rng) -> str:
        w = wishes[0]
        return say(rng.choice(phrases(s, "wish_ack_phrases")), verb=w.verb, text=w.text, category=w.category)

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
            return rng.choice([f"你還沒跟我說過想{verb}什麼耶～下次聊到「我想{verb}…」我就幫你記下來。",
                               f"清單是空的😅 有想{verb}的跟我說一聲，我幫你記著。"])
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
            c = credit[n["id"]] - (total / k if n in picked and len(items) > k else 0)
            if len(items) <= k:
                c = 0.0
            self.store.update_note(n["id"], credit=max(-total, min(total, c)))
        head = say(rng.choice(phrases(s, "suggest_phrases")), verb=verb)
        return head + "\n" + "\n".join(f"・{n['text']}（{self._ago(n, now)}）" for n in picked)

    def _ago(self, n: dict, now: datetime) -> str:
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

    # ---------- 提醒 ----------
    def _targets(self, msg: Incoming, s: dict) -> tuple[list[str], bool]:
        targets = []
        if s["feature_mention_others"]:
            targets = [f"u:{u}" for u in msg.mention_user_ids] + [f"r:{r}" for r in msg.mention_role_ids]
        return (targets, True) if targets else ([f"u:{msg.author.id}"], False)

    def _reminder(self, msg: Incoming, it: Intent, s: dict, now: datetime, rng) -> list:
        ts = int(now.timestamp())
        targets, others = self._targets(msg, s)
        who = f" {' '.join(map(mention_str, targets))} " if others else "你"
        extra = {k: v for k, v in (("verb", it.verb), ("max_count", it.max_count),
                                   ("until", int(it.until.timestamp()) if it.until else None),
                                   ("limit", it.limit_label), ("offset", it.offset), ("event", it.event))
                 if v}
        base = dict(channel_id=msg.channel_id, creator_id=msg.author.id, targets=targets, text=it.text, now=ts)

        if it.rain:
            rid = self.store.add_reminder(kind="rain", due_at=FAR_FUTURE, extra=extra, **base)
            line = say(rng.choice(phrases(s, "confirm_phrases")), when="下雨天", verb=it.verb, who=who,
                       text=it.text or "帶傘")
            return self._reply(msg, line + (f"\n-# ☔ #{rid}・每天 {s['weather_daily_time']} 看預報，"
                                            f"降雨機率 {s['weather_rain_threshold']}% 以上就提醒"
                                            if s["show_exact_time"] else ""), [cancel_button(rid)])

        if it.payday:
            extra.update(payday=True, payday_repeat=bool(it.repeat))
            when = self._next_payday(msg.author.id, now, s)
            if when is None:
                rid = self.store.add_reminder(due_at=0, status="draft", extra=extra, **base)
                return self._reply(msg, "你的發薪日是每個月幾號呀？跟我說「發薪日是 5 號」我就記住～", reminder_id=rid)
            day = when.day
            it.when, it.repeat, it.phrase = when, (f"monthly:{day}" if it.repeat else None), \
                ("每個發薪日" if it.repeat else "發薪日")

        if it.ask:
            rid = self.store.add_reminder(due_at=0, status="draft", extra=extra, **base)
            return self._ask(msg, it, s, now, rng, rid, who)

        if it.max_count and not it.repeat:
            extra["nag_max"] = it.max_count - 1        # 「提醒我三次」= 響一次 + 再提醒兩次
        rid = self.store.add_reminder(due_at=int(it.when.timestamp()), repeat=it.repeat, extra=extra, **base)
        return self._reply(msg, self._confirm_line(rid, it.when, it.repeat, it.phrase, it.dated, it.verb, who,
                                                   it.text, it.limit_label, s, now, rng), [cancel_button(rid)])

    def _confirm_line(self, rid, when, repeat, phrase, dated, verb, who, text, limit, s, now, rng) -> str:
        echo = self._echo(phrase, when, dated, now)
        line = say(rng.choice(phrases(s, "confirm_phrases")), when=echo, verb=verb, who=who, text=text)
        if limit:
            line = re.sub(r"([。！～!]?)$", f"（{limit}）\\1", line, count=1)
        if s["show_exact_time"]:
            rep = timeparse.describe_repeat(repeat)
            line += f"\n-# 📌 #{rid}・{timeparse.fmt_dt(when, self.tz)}{'・' + rep if rep else ''}"
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

    def _ask(self, msg: Incoming, it: Intent, s: dict, now, rng, rid: int, who: str) -> list:
        if it.ask == "vague":
            v = it.event
            q = rng.choice([f"「{v}」是指什麼時候呢？", f"我確認一下，你說的「{v}」大概是什麼時候？",
                            f"好～那「{v}」大概是什麼時候比較好？"])
        else:
            q = say(rng.choice(phrases(s, "ask_phrases")), verb=it.verb, who=who, text=it.text)
            if it.ask == "event":
                q = f"我沒辦法知道你什麼時候{it.event}，" + q
        q += "\n-# 可以按下面的按鈕，或直接打字回我，例如「明天晚上8點」"
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
        if ex.get("offset"):
            phrase = f"{phrase}再過 {fmt_seconds(ex['offset'])}"
        fields = dict(status="pending", due_at=int(when.timestamp()), repeat=repeat)
        if ex.get("max_count") and not repeat:
            ex["nag_max"] = ex["max_count"] - 1
            fields["extra"] = ex
        self.store.update_reminder(draft["id"], **fields)
        others = draft["targets"] != [f"u:{draft['creator_id']}"]
        who = f" {' '.join(map(mention_str, draft['targets']))} " if others else "你"
        line = self._confirm_line(draft["id"], when, repeat, phrase, dated, ex.get("verb", "提醒"), who,
                                  draft["text"], ex.get("limit", ""), s, now, rng)
        out = []
        if draft["message_id"]:
            out.append(Edit(draft["channel_id"], draft["message_id"], append="-# ✅ 已設定"))
        out.append(Send(channel_id, line, [cancel_button(draft["id"])], reply_to=reply_to))
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
            return self._reply(msg, "目前沒有剛響過的提醒耶，要延後哪一個呢？可以「回覆」那則提醒再跟我說一次。")
        if not can_touch(rem, msg.author):
            return self._reply(msg, "這不是你的提醒喔。")
        if it.ask:
            lead = f"我沒辦法知道你什麼時候{it.event}，" if it.ask == "event" else ""
            return self._reply(msg, f"{lead}要把「{rem['text'] or '這個提醒'}」延到什麼時候？",
                               self._pick_buttons("snz", rem["id"], now, s))
        when = it.when
        return self._apply_snooze(rem, when, self._echo(it.phrase, when, it.dated, now), msg.channel_id,
                                  msg.message_id, s, rng)

    def _apply_snooze(self, rem: dict, when: datetime, echo: str, channel_id: int, reply_to, s, rng) -> list:
        self.store.update_reminder(rem["id"], status="pending", due_at=int(when.timestamp()), nag_count=0)
        out = []
        if rem["message_id"]:
            out.append(Edit(rem["channel_id"], rem["message_id"],
                            append=f"-# 💤 延到 {timeparse.fmt_dt(when, self.tz)}"))
        verb = rem["extra"].get("verb", "提醒")
        line = say(rng.choice(phrases(s, "snooze_phrases")), when=echo, verb=verb, who="你", text=rem["text"])
        if s["show_exact_time"]:
            line += f"\n-# 💤 #{rem['id']}「{rem['text'] or '提醒'}」・{timeparse.fmt_dt(when, self.tz)}"
        out.append(Send(channel_id, line, reply_to=reply_to))
        return out

    # ---------- 用內容取消 ----------
    def _cancel_match(self, msg: Incoming, it: Intent, now: datetime) -> list:
        uid = msg.author.id
        if it.scope == "notes":
            notes = self.store.user_notes(uid)
            hits = [n for n in notes if it.all and not it.keyword or fuzzy_match(it.keyword, n["text"])]
            if not hits:
                return self._reply(msg, f"找不到{'跟「' + it.keyword + '」有關的' if it.keyword else ''}記事耶。")
            ids, lines = [n["id"] for n in hits], [f"・#{n['id']}（{n['category']}）{n['text']}" for n in hits[:10]]
            kind, what = "notes", "記事"
        else:
            rems = [r for r in self.store.user_reminders(uid) if r["kind"] != "draft"]
            if it.day is not None:
                day = now.astimezone(self.tz).date() + timedelta(days=it.day)
                rems = [r for r in rems if datetime.fromtimestamp(r["due_at"], self.tz).date() == day]
            if it.keyword:
                rems = [r for r in rems if fuzzy_match(it.keyword, r["text"])]
            elif not it.all and it.day is None:
                return self._reply(msg, "要取消哪一個呢？\n" + self.list_text(uid, "reminders", None))
            if not rems:
                return self._reply(msg, f"找不到{'跟「' + it.keyword + '」有關的' if it.keyword else '符合的'}提醒耶。")
            ids = [r["id"] for r in rems]
            lines = [f"・#{r['id']} {self._when_label(r)} {r['text']}" for r in rems[:10]]
            kind, what = "reminders", "提醒"
        token = int(self.store.get_meta("bulk_seq", 0)) + 1
        self.store.set_meta("bulk_seq", token)
        self.store.set_meta(f"bulk:{token}", {"user": uid, "kind": kind, "ids": ids})
        more = f"\n…還有 {len(ids) - 10} 個" if len(ids) > 10 else ""
        verb = "刪掉" if kind == "notes" else "取消"
        return self._reply(msg, f"這 {len(ids)} 個{what}要{verb}嗎？\n" + "\n".join(lines) + more,
                           [Button(f"rem:bulk:{token}:1", f"確認{verb}", "🗑️", "danger"),
                            Button(f"rem:bulk:{token}:0", "先不要")])

    def _when_label(self, r: dict) -> str:
        if r["kind"] == "rain":
            return "下雨天"
        rep = timeparse.describe_repeat(r["repeat"])
        return timeparse.fmt_dt(datetime.fromtimestamp(r["due_at"], self.tz), self.tz) + (f"（{rep}）" if rep else "")

    def list_text(self, user_id: int, scope: str, category: str | None) -> str:
        out = []
        if scope in ("reminders", "all"):
            rems = self.store.user_reminders(user_id)
            out.append("📋 **你的提醒**" if rems else "📋 目前沒有提醒。")
            for r in rems[:30]:
                bits = [f"`#{r['id']}` {self._when_label(r)} {r['text']}"]
                if r["kind"] == "timer":
                    bits.append("⏱️")
                if r["status"] == "fired":
                    bits.append("（響過了，還沒按收到）")
                if r["targets"] != [f"u:{user_id}"]:
                    bits.append("→ " + " ".join(map(mention_str, r["targets"])))
                out.append(" ".join(bits))
        if scope in ("notes", "all"):
            notes = self.store.user_notes(user_id, category)
            title = f"📝 **你的「{category}」**" if category else "📝 **你的記事**"
            out.append(title if notes else f"{title}：還沒有東西。")
            last = None
            for n in notes[:60]:
                if not category and n["category"] != last:
                    out.append(f"__{n['category']}__")
                    last = n["category"]
                out.append(f"`#{n['id']}` {n['text']}")
        return "\n".join(out)[:2000]

    # ---------- 按鈕 ----------
    def press(self, custom_id: str, user: User, now: datetime) -> PressResult:
        m = re.fullmatch(r"rem:(done|snooze|cancel|pick|snz|bulk):(\d+)(?::([0-9a-z]+))?", custom_id)
        if not m:
            return PressResult(ephemeral="這個按鈕已經失效了。")
        action, rid, arg = m.group(1), int(m.group(2)), m.group(3)
        s = self.store.settings()
        rng = random.Random(rid * 7919 + int(now.timestamp()))
        who = user.name or "你"

        if action == "bulk":
            job = self.store.get_meta(f"bulk:{rid}")
            if not job:
                return PressResult(ephemeral="這個確認已經過期了。")
            if job["user"] != user.id:
                return PressResult(ephemeral="這不是你的喔。")
            self.store.set_meta(f"bulk:{rid}", None)
            if arg != "1":
                return PressResult(append="-# 好，先不動。")
            for i in job["ids"]:
                if job["kind"] == "notes":
                    self.store.delete_note(i, user.id)
                else:
                    self.store.update_reminder(i, status="cancelled")
            return PressResult(append=f"-# 🗑️ 已{'刪掉' if job['kind'] == 'notes' else '取消'} {len(job['ids'])} 個")

        rem = self.store.get_reminder(rid)
        if not rem:
            return PressResult(ephemeral="找不到這個提醒。")
        if not can_touch(rem, user):
            return PressResult(ephemeral="這不是你的提醒喔。")

        if action == "pick":
            if rem["status"] != "draft":
                return PressResult(ephemeral="這個提醒已經設定好了。")
            when = self._choice_dt(arg or "", now, s)
            label = dict(PICKS).get(arg, "")
            acts = self._finish_draft(rem, when, None, label, True, rem["channel_id"], None, now, rng)
            return PressResult(append=f"-# ✅ 選了：{label}", actions=[a for a in acts if isinstance(a, Send)])
        if action == "snz":
            when = self._choice_dt(arg or "", now, s)
            label = dict(PICKS).get(arg, "")
            acts = self._apply_snooze(rem, when, label, rem["channel_id"], None, s, rng)
            return PressResult(append=f"-# 💤 選了：{label}", actions=acts)
        if action == "done":
            self.store.update_reminder(rid, status="done")
            return PressResult(append=f"-# ✅ {who} {s['btn_done_label']}")
        if action == "cancel":
            if rem["status"] in ("done", "cancelled"):
                return PressResult(ephemeral="這個提醒已經結束了。")
            self.store.update_reminder(rid, status="cancelled")
            return PressResult(append="-# 🗑️ 已取消", strike=True)
        when = self._choice_dt(arg or str(s["btn_later_minutes"]), now, s)
        self.store.update_reminder(rid, status="pending", due_at=int(when.timestamp()), nag_count=0)
        return PressResult(append=f"-# 💤 {who}：{timeparse.fmt_dt(when, self.tz)} 再提醒")

    # ---------- 時間到 ----------
    def tick(self, now: datetime, default_channel_id: int | None = None) -> list:
        s = self.store.settings()
        ts = int(now.timestamp())
        actions: list = []

        for rem in self.store.due_reminders(ts):
            mentions = " ".join(map(mention_str, rem["targets"]))
            if rem["kind"] == "timer":
                content = fill(s["timer_template"], mentions=mentions, text=rem["text"],
                               duration=rem["extra"].get("duration", ""))
            else:
                content = fill(s["reminder_template"], mentions=mentions, text=rem["text"] or "時間到了！",
                               time=timeparse.fmt_dt(datetime.fromtimestamp(rem["due_at"], self.tz), self.tz),
                               creator=f"<@{rem['creator_id']}>", repeat=timeparse.describe_repeat(rem["repeat"]))
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
                    content += "\n-# 這是最後一次囉"
                    self.store.update_reminder(rem["id"], status="done", extra=ex)
                else:
                    self.store.update_reminder(rem["id"], due_at=int(nxt.timestamp()), extra=ex)
                target_id = occ
            else:
                self.store.update_reminder(rem["id"], status="fired", fired_at=ts, nag_count=0)
                target_id = rem["id"]
            actions.append(Send(rem["channel_id"], content[:2000], fired_buttons(target_id, s),
                                ping=True, reminder_id=target_id))

        before = ts - int(s["nag_interval_minutes"]) * 60
        for rem in self.store.nag_candidates(before, 99):
            limit = rem["extra"].get("nag_max", s["nag_max"] if s["nag_enabled"] else 0)
            if rem["nag_count"] >= int(limit):
                continue
            if rem["message_id"]:
                actions.append(Edit(rem["channel_id"], rem["message_id"], clear_buttons=True))
            content = fill(s["nag_template"], mentions=" ".join(map(mention_str, rem["targets"])),
                           text=rem["text"] or "時間到了！", count=rem["nag_count"] + 2)
            if rem["nag_count"] + 1 >= int(limit) and "nag_max" in rem["extra"]:
                content += "\n-# 這是最後一次囉"
            self.store.update_reminder(rem["id"], fired_at=ts, nag_count=rem["nag_count"] + 1)
            actions.append(Send(rem["channel_id"], content[:2000], fired_buttons(rem["id"], s),
                                ping=True, reminder_id=rem["id"]))

        if s["feature_weather"]:
            has_rain = any(r["kind"] == "rain" for r in self.store.all_reminders(("pending",)))
            local = now.astimezone(self.tz)
            h, m = map(int, s["weather_daily_time"].split(":"))
            target = datetime.combine(local.date(), time(h, m), self.tz)
            if (s["weather_daily_enabled"] or has_rain) and target <= local < target + timedelta(minutes=30) \
                    and self.store.get_meta("weather_sent") != local.date().isoformat():
                self.store.set_meta("weather_sent", local.date().isoformat())
                actions.append(NeedWeather(s["weather_county"], default_channel_id,
                                           post=bool(s["weather_daily_enabled"]), rain_check=has_rain))
        return actions

    def rain_check(self, periods: list, now: datetime) -> list:
        """每天看完預報後呼叫：今天會下雨就把「下雨天提醒」送出去。"""
        s = self.store.settings()
        today = now.astimezone(self.tz).date()
        pops = [p.pop for p in periods if p.start.date() <= today <= p.end.date()] or [p.pop for p in periods[:2]]
        pop = max(pops) if pops else 0
        if pop < int(s["weather_rain_threshold"]):
            return []
        ts, actions = int(now.timestamp()), []
        for rem in self.store.all_reminders(("pending",)):
            if rem["kind"] != "rain":
                continue
            actions += self._expire_previous(rem)
            occ = self._occurrence(rem, ts)
            content = fill(s["rain_template"], mentions=" ".join(map(mention_str, rem["targets"])),
                           text=rem["text"] or "記得帶傘", pop=pop)
            actions.append(Send(rem["channel_id"], content[:2000], fired_buttons(occ, s), ping=True,
                                reminder_id=occ))
        return actions

    def _occurrence(self, rem: dict, ts: int) -> int:
        """重複提醒／下雨天提醒：這一次另外存一筆（按鈕、延後都對這筆）。"""
        occ = self.store.add_reminder(
            channel_id=rem["channel_id"], creator_id=rem["creator_id"], targets=rem["targets"], text=rem["text"],
            due_at=ts if rem["kind"] == "rain" else rem["due_at"], parent_id=rem["id"], status="fired", now=ts,
            extra={"verb": rem["extra"].get("verb", "提醒")})
        self.store.update_reminder(occ, fired_at=ts)
        return occ

    def _expire_previous(self, rem: dict) -> list:
        """上一次還沒按收到的就算過期，不要跟這次一起再提醒。"""
        out = []
        for old in self.store.all_reminders(("fired",)):
            if old["parent_id"] == rem["id"]:
                self.store.update_reminder(old["id"], status="expired")
                if old["message_id"]:
                    out.append(Edit(old["channel_id"], old["message_id"], append="-# ⏭️ 已過期"))
        return out

    def sent(self, reminder_id: int, message_id: int):
        """Send 帶 reminder_id 的訊息送出後呼叫，記下訊息編號（延後、再提醒、回答問句要用）。"""
        self.store.update_reminder(reminder_id, message_id=message_id)

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


def can_touch(rem: dict, user: User) -> bool:
    if rem["creator_id"] == user.id or f"u:{user.id}" in rem["targets"]:
        return True
    return any(t.startswith("r:") and int(t[2:]) in user.role_ids for t in rem["targets"])


def fired_buttons(rid: int, s: dict) -> list[Button]:
    return [Button(f"rem:done:{rid}", s["btn_done_label"], "✅", "success"),
            Button(f"rem:snooze:{rid}:{int(s['btn_later_minutes'])}", s["btn_later_label"], "⏳"),
            Button(f"rem:snooze:{rid}:tmr", s["btn_tomorrow_label"], "📅")]


def cancel_button(rid: int) -> Button:
    return Button(f"rem:cancel:{rid}", "取消這個提醒")
