"""助理的核心邏輯，不碰 Discord、不碰網路。

輸入：一則訊息、按下一個按鈕、或「現在幾點了」（tick）。
輸出：要做的動作清單（送訊息、改訊息、查天氣）。
正式的 Discord bot（bot.py）和測試網頁（sim/）都用同一份。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from . import timeparse
from .router import Intent, route

HELP = """**我會做這些事**（@我 再說一句話就好，順序隨意）
⏰ **提醒**：`明天下午3點提醒我交報告`、`提醒我週五繳電費`、`10分鐘後叫我關火`
🔁 **重複**：`每天早上8點提醒我吃藥`、`每週一三五晚上9點提醒運動`、`每月5號提醒繳房租`
👥 **提醒別人**：`明天中午提醒 @某人 開會`
⏱️ **計時**：`計時 25 分鐘`、`倒數 1小時30分 泡麵`
📝 **記住**：`記住 靈感：…`、`我想看 葬送的芙莉蓮`、`記一下 車停在 B2-37`
📋 **查詢**：`我有哪些提醒`、`我想看什麼`、`列出靈感`、`我的記事`
🗑️ **取消／刪除**：`取消 12`、`刪除靈感 3`
💤 **延後**：提醒響了按按鈕，或回覆那則提醒說 `延後 30 分鐘`
🌤️ **天氣**：`天氣`、`台中天氣`"""

SNOOZE_WORDS = r"延後|晚點|等等|等一下"


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


@dataclass
class PressResult:
    ephemeral: str | None = None                # 只給按的人看的訊息（不改原訊息）
    append: str = ""
    strike: bool = False


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


def fmt_minutes(m: int) -> str:
    if m % 1440 == 0:
        return f"{m // 1440} 天後"
    if m % 60 == 0:
        return f"{m // 60} 小時後"
    return f"{m} 分鐘後"


class Assistant:
    def __init__(self, store, tz):
        self.store = store
        self.tz = tz
        self.last_intent: Intent | None = None   # 測試網頁顯示「判斷結果」用

    # ---------- 收訊息 ----------
    def handle(self, msg: Incoming, now: datetime) -> list:
        s = self.store.settings()
        text = re.sub(r"<@[!&]?\d+>", " ", msg.text)
        it = route(text, now, s)
        if msg.reply_to_bot_message_id and re.search(SNOOZE_WORDS, text):
            it = Intent("snooze", text="tomorrow" if re.search(r"明天|明早", text) else "",
                        seconds=it.seconds if it.kind == "snooze" else None)
        self.last_intent = it
        return self._dispatch(msg, it, s, now)

    def _dispatch(self, msg: Incoming, it: Intent, s: dict, now: datetime) -> list:
        def reply(text, buttons=None):
            return [Send(msg.channel_id, text[:2000], buttons or [], reply_to=msg.message_id)]
        ts = int(now.timestamp())

        if it.error:
            return reply(f"🤔 {it.error}")
        if it.kind == "help":
            return reply(HELP)

        if it.kind == "reminder":
            if not s["feature_reminder"]:
                return reply("提醒功能目前關閉中。")
            targets = []
            if s["feature_mention_others"]:
                targets = [f"u:{u}" for u in msg.mention_user_ids] + [f"r:{r}" for r in msg.mention_role_ids]
            others = bool(targets)
            targets = targets or [f"u:{msg.author.id}"]
            text = it.text or "時間到了！"
            rid = self.store.add_reminder(channel_id=msg.channel_id, creator_id=msg.author.id, targets=targets,
                                          text=text, due_at=int(it.when.timestamp()), repeat=it.repeat, now=ts)
            rep = timeparse.describe_repeat(it.repeat)
            return reply(fill(s["confirm_template"], id=rid, time=timeparse.fmt_dt(it.when, self.tz),
                              repeat=f"（{rep}）" if rep else "", text=text,
                              targets=(" → 提醒 " + " ".join(map(mention_str, targets))) if others else ""),
                         [cancel_button(rid)])

        if it.kind == "timer":
            if not s["feature_timer"]:
                return reply("計時功能目前關閉中。")
            due = now + timedelta(seconds=it.seconds)
            rid = self.store.add_reminder(
                kind="timer", channel_id=msg.channel_id, creator_id=msg.author.id,
                targets=[f"u:{msg.author.id}"], text=it.text, due_at=int(due.timestamp()), now=ts,
                extra={"duration": fmt_seconds(it.seconds)})
            label = f"「{it.text}」" if it.text else ""
            return reply(f"⏱️ 開始計時 {fmt_seconds(it.seconds)}（#{rid}），"
                         f"{due.astimezone(self.tz):%H:%M:%S} 叫你。{label}", [cancel_button(rid)])

        if it.kind == "note":
            if not s["feature_note"]:
                return reply("記事功能目前關閉中。")
            nid = self.store.add_note(msg.author.id, it.category, it.text, ts)
            return reply(fill(s["note_saved_template"], id=nid, category=it.category, text=it.text))

        if it.kind == "list":
            return reply(self.list_text(msg.author.id, it.scope, it.category))

        if it.kind == "cancel":
            done, denied = [], []
            for rid in it.ids:
                rem = self.store.get_reminder(rid)
                if not rem or rem["status"] in ("done", "cancelled"):
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

        if it.kind == "snooze":
            if not msg.reply_to_bot_message_id:
                return reply("要延後哪個提醒？請「回覆」那則提醒訊息再說「延後 30 分鐘」，或直接按按鈕。")
            rem = self.store.reminder_by_message(msg.reply_to_bot_message_id)
            if not rem:
                return reply("這則訊息不是提醒喔。")
            if not can_touch(rem, msg.author):
                return reply("這不是你的提醒喔。")
            arg = "tmr" if it.text == "tomorrow" else str(max(1, (it.seconds or 600) // 60))
            new_due = self._snooze(rem, arg, now)
            when = timeparse.fmt_dt(new_due, self.tz)
            return [Edit(rem["channel_id"], msg.reply_to_bot_message_id, append=f"-# 💤 已延後到 {when}"),
                    *reply(f"💤 好，{when} 再提醒。")]

        if it.kind == "weather":
            if not s["feature_weather"]:
                return reply("天氣功能目前關閉中。")
            return [NeedWeather(it.county or s["weather_county"], msg.channel_id, reply_to=msg.message_id)]

        return reply("🤔 我聽不太懂，試試「明天下午3點提醒我…」「記住…」「計時10分鐘」，或說「說明」看全部功能。")

    def list_text(self, user_id: int, scope: str, category: str | None) -> str:
        out = []
        if scope in ("reminders", "all"):
            rems = self.store.user_reminders(user_id)
            out.append("📋 **你的提醒**" if rems else "📋 目前沒有提醒。")
            for r in rems[:30]:
                when = timeparse.fmt_dt(datetime.fromtimestamp(r["due_at"], self.tz), self.tz)
                bits = [f"`#{r['id']}` {when} {r['text']}"]
                if r["kind"] == "timer":
                    bits.append("⏱️")
                if r["repeat"]:
                    bits.append("🔁" + timeparse.describe_repeat(r["repeat"]))
                if r["status"] == "fired":
                    bits.append("（響過了，還沒按完成）")
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
        m = re.fullmatch(r"rem:(done|snooze|cancel):(\d+)(?::(\d+|tmr))?", custom_id)
        if not m:
            return PressResult(ephemeral="這個按鈕已經失效了。")
        action, rid, arg = m.group(1), int(m.group(2)), m.group(3)
        rem = self.store.get_reminder(rid)
        if not rem:
            return PressResult(ephemeral="找不到這個提醒。")
        if not can_touch(rem, user):
            return PressResult(ephemeral="這不是你的提醒喔。")
        who = user.name or "你"
        if action == "done":
            self.store.update_reminder(rid, status="done")
            return PressResult(append=f"-# ✅ {who} 已完成")
        if action == "cancel":
            if rem["status"] in ("done", "cancelled"):
                return PressResult(ephemeral="這個提醒已經結束了。")
            self.store.update_reminder(rid, status="cancelled")
            return PressResult(append="-# 🗑️ 已取消", strike=True)
        new_due = self._snooze(rem, arg or "10", now)
        return PressResult(append=f"-# 💤 {who} 延後到 {timeparse.fmt_dt(new_due, self.tz)}")

    def _snooze(self, rem: dict, arg: str, now: datetime) -> datetime:
        s = self.store.settings()
        if arg == "tmr":
            h, m = map(int, s["time_morning"].split(":"))
            new_due = datetime.combine(now.astimezone(self.tz).date() + timedelta(days=1), time(h, m), self.tz)
        else:
            new_due = now + timedelta(minutes=int(arg))
        self.store.update_reminder(rem["id"], status="pending", due_at=int(new_due.timestamp()), nag_count=0)
        return new_due

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
                content = fill(s["reminder_template"], mentions=mentions, text=rem["text"],
                               time=timeparse.fmt_dt(datetime.fromtimestamp(rem["due_at"], self.tz), self.tz),
                               creator=f"<@{rem['creator_id']}>", repeat=timeparse.describe_repeat(rem["repeat"]))
            if rem["repeat"]:
                # 上一次還沒按完成的就算過期，不要跟這次一起再提醒
                for old in self.store.all_reminders(("fired",)):
                    if old["parent_id"] == rem["id"]:
                        self.store.update_reminder(old["id"], status="expired")
                        if old["message_id"]:
                            actions.append(Edit(old["channel_id"], old["message_id"], append="-# ⏭️ 已過期"))
                # 重複提醒：這一次另外存一筆（按鈕、延後都對這筆），本體排到下一次
                occ = self.store.add_reminder(
                    channel_id=rem["channel_id"], creator_id=rem["creator_id"], targets=rem["targets"],
                    text=rem["text"], due_at=rem["due_at"], parent_id=rem["id"], status="fired", now=ts)
                self.store.update_reminder(occ, fired_at=ts)
                due = datetime.fromtimestamp(rem["due_at"], self.tz)
                nxt = timeparse.next_occurrence(rem["repeat"], max(now, due), due.time(), self.tz)
                self.store.update_reminder(rem["id"], due_at=int(nxt.timestamp()))
                target_id = occ
            else:
                self.store.update_reminder(rem["id"], status="fired", fired_at=ts, nag_count=0)
                target_id = rem["id"]
            actions.append(Send(rem["channel_id"], content[:2000], fired_buttons(target_id, s),
                                ping=True, reminder_id=target_id))

        if s["nag_enabled"] and s["nag_max"] > 0:
            before = ts - int(s["nag_interval_minutes"]) * 60
            for rem in self.store.nag_candidates(before, int(s["nag_max"])):
                if rem["message_id"]:
                    actions.append(Edit(rem["channel_id"], rem["message_id"], clear_buttons=True))
                content = fill(s["nag_template"], mentions=" ".join(map(mention_str, rem["targets"])),
                               text=rem["text"], count=rem["nag_count"] + 2)
                self.store.update_reminder(rem["id"], fired_at=ts, nag_count=rem["nag_count"] + 1)
                actions.append(Send(rem["channel_id"], content[:2000], fired_buttons(rem["id"], s),
                                    ping=True, reminder_id=rem["id"]))

        if s["feature_weather"] and s["weather_daily_enabled"]:
            local = now.astimezone(self.tz)
            h, m = map(int, s["weather_daily_time"].split(":"))
            target = datetime.combine(local.date(), time(h, m), self.tz)
            if target <= local < target + timedelta(minutes=30) \
                    and self.store.get_meta("weather_sent") != local.date().isoformat():
                self.store.set_meta("weather_sent", local.date().isoformat())
                actions.append(NeedWeather(s["weather_county"], default_channel_id))
        return actions

    def sent(self, reminder_id: int, message_id: int):
        """Send 帶 reminder_id 的訊息送出後呼叫，記下訊息編號（延後、再提醒要用）。"""
        self.store.update_reminder(reminder_id, message_id=message_id)

    def next_event_at(self, now: datetime) -> datetime | None:
        """測試網頁「跳到下一件事」用：下一個提醒或再提醒的時間。"""
        s = self.store.settings()
        times = [r["due_at"] for r in self.store.all_reminders(("pending",))]
        if s["nag_enabled"] and s["nag_max"] > 0:
            iv = int(s["nag_interval_minutes"]) * 60
            times += [(r["fired_at"] or 0) + iv for r in self.store.all_reminders(("fired",))
                      if r["kind"] == "reminder" and r["nag_count"] < int(s["nag_max"])]
        future = [t for t in times if t > now.timestamp()]
        return datetime.fromtimestamp(min(future), self.tz) if future else None


def can_touch(rem: dict, user: User) -> bool:
    if rem["creator_id"] == user.id or f"u:{user.id}" in rem["targets"]:
        return True
    return any(t.startswith("r:") and int(t[2:]) in user.role_ids for t in rem["targets"])


def fired_buttons(rid: int, s: dict) -> list[Button]:
    out = [Button(f"rem:done:{rid}", "完成", "✅", "success")]
    for m in str(s["snooze_minutes"]).split(","):
        if m.strip().isdigit():
            out.append(Button(f"rem:snooze:{rid}:{m.strip()}", fmt_minutes(int(m.strip())), "💤"))
    if s["snooze_tomorrow"]:
        out.append(Button(f"rem:snooze:{rid}:tmr", "明天", "💤"))
    return out


def cancel_button(rid: int) -> Button:
    return Button(f"rem:cancel:{rid}", "取消這個提醒")
