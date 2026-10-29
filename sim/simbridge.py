"""測試網頁用：在瀏覽器（Pyodide）裡模擬一個頻道，核心邏輯和正式 bot 完全同一份。

JS 只負責畫面；所有判斷都在這裡呼叫 reminderbot.core。每個方法回傳 JSON 字串。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from reminderbot import timeparse, weather
from reminderbot.core import Assistant, Edit, Incoming, NeedWeather, Send, User
from reminderbot.settings import COUNTIES, DEFAULTS, GROUPS, HINTS, LABELS, validate
from reminderbot.store import MemoryStore

TZ = timezone(timedelta(hours=8))
BOT_ID = 1000
CHANNEL = 1
MEMBERS = {
    1: User(1, "你", {50}),
    2: User(2, "小明", {50}),
    3: User(3, "小美", set()),
}
ROLES = {50: "樂團"}


class Sim:
    def __init__(self, start_ms: float):
        self.store = MemoryStore()
        self.core = Assistant(self.store, TZ)
        self.now = datetime.fromtimestamp(start_ms / 1000, TZ).replace(second=0, microsecond=0)
        self.messages: list[dict] = []
        self.next_id = 1
        self.store.save_settings({"weather_county": "臺北市"})

    # ---------- 給 JS 的方法 ----------
    def info(self) -> str:
        return json.dumps({
            "members": [{"id": u.id, "name": u.name, "roles": sorted(u.role_ids)} for u in MEMBERS.values()],
            "roles": [{"id": k, "name": v} for k, v in ROLES.items()],
            "bot": {"id": BOT_ID, "name": "jvs"},
            "defaults": DEFAULTS, "labels": LABELS, "hints": HINTS, "groups": GROUPS, "counties": COUNTIES,
        }, ensure_ascii=False)

    def say(self, user_id: int, text: str, reply_to: int | None = None) -> str:
        user = MEMBERS[int(user_id)]
        raw, mentions, roles = self._mentions(text)
        mid = self._add(author=user.id, text=raw, reply_to=reply_to)
        reply_bot = reply_to if reply_to and self._msg(reply_to) and self._msg(reply_to)["author"] == BOT_ID else None
        actions = self.core.handle(Incoming(user, raw, CHANNEL, mid, mentions, roles, reply_bot), self.now)
        self._msg(mid)["intent"] = intent_summary(self.core.last_intent, TZ)
        self._run(actions)
        return self.state()

    def press(self, user_id: int, message_id: int, custom_id: str) -> str:
        user = MEMBERS[int(user_id)]
        res = self.core.press(custom_id, user, self.now)
        msg = self._msg(int(message_id))
        if res.ephemeral:
            self._add(author=BOT_ID, text=res.ephemeral, ephemeral_for=user.id)
        elif msg:
            if res.strike:
                msg["text"] = f"~~{msg['text']}~~"
            if res.append:
                msg["text"] += "\n" + res.append
            msg["buttons"] = []
        return self.state()

    def advance(self, seconds: int) -> str:
        """時間往前走，每分鐘跑一次排程（正式 bot 是每 15 秒）。"""
        end = self.now + timedelta(seconds=int(seconds))
        while self.now < end:
            self.now = min(self.now + timedelta(minutes=1), end)
            self._run(self.core.tick(self.now, CHANNEL))
        return self.state()

    def jump_next(self) -> str:
        nxt = self.core.next_event_at(self.now)
        if nxt is None:
            return self.state(note="目前沒有等待中的提醒。")
        return self.advance(int((nxt - self.now).total_seconds()) + 1)

    def set_time(self, iso_local: str) -> str:
        target = datetime.fromisoformat(iso_local).replace(tzinfo=TZ)
        if target > self.now:
            return self.advance(int((target - self.now).total_seconds()))
        self.now = target     # 往回撥不跑排程
        return self.state()

    def save_settings(self, form_json: str) -> str:
        values, errors = validate(json.loads(form_json))
        self.store.save_settings(values)
        return self.state(note="；".join(errors) if errors else "設定已套用。")

    def reset_settings(self) -> str:
        self.store.save_settings(DEFAULTS)
        self.store.save_settings({"weather_county": "臺北市"})
        return self.state(note="設定已恢復預設。")

    def clear(self) -> str:
        now = self.now
        self.__init__(now.timestamp() * 1000)
        return self.state()

    def state(self, note: str = "") -> str:
        rems = self.store.all_reminders(("pending", "fired", "done", "cancelled", "expired"))
        return json.dumps({
            "now": self.now.isoformat(),
            "now_label": timeparse.fmt_dt(self.now, TZ),
            "messages": self.messages,
            "reminders": [{
                "id": r["id"], "text": r["text"], "status": r["status"], "kind": r["kind"],
                "when": timeparse.fmt_dt(datetime.fromtimestamp(r["due_at"], TZ), TZ),
                "repeat": timeparse.describe_repeat(r["repeat"]), "parent": r["parent_id"],
                "creator": MEMBERS.get(r["creator_id"], User(0, "?")).name,
                "targets": [target_name(t) for t in r["targets"]], "nags": r["nag_count"],
            } for r in rems],
            "notes": [{"id": n["id"], "category": n["category"], "text": n["text"],
                       "user": MEMBERS.get(n["user_id"], User(0, "?")).name} for n in self.store.all_notes()],
            "settings": self.store.settings(),
            "next_event": (lambda t: timeparse.fmt_dt(t, TZ) if t else None)(self.core.next_event_at(self.now)),
            "note": note,
        }, ensure_ascii=False)

    # ---------- 內部 ----------
    def _mentions(self, text: str):
        """把「@小明」「@樂團」換成 Discord 的 <@id>／<@&id>，就像真的在 Discord 選了成員。"""
        mentions, roles = [], []

        def sub(m):
            name = m.group(1)
            if name.lower() == "jvs":
                return f"<@{BOT_ID}>"
            for u in MEMBERS.values():
                if u.name == name:
                    mentions.append(u.id)
                    return f"<@{u.id}>"
            for rid, rname in ROLES.items():
                if rname == name:
                    roles.append(rid)
                    return f"<@&{rid}>"
            return m.group(0)
        names = sorted([u.name for u in MEMBERS.values()] + list(ROLES.values()) + ["jvs"], key=len, reverse=True)
        raw = re.sub("@(" + "|".join(map(re.escape, names)) + ")", sub, text, flags=re.I)
        if f"<@{BOT_ID}>" not in raw:
            raw = f"<@{BOT_ID}> {raw}"          # 測試網頁裡每句都當作有 @jvs
        return raw, mentions, roles

    def _add(self, author: int, text: str, reply_to=None, buttons=None, ping=False, ephemeral_for=None) -> int:
        mid = self.next_id
        self.next_id += 1
        self.messages.append({
            "id": mid, "author": author, "text": text, "reply_to": reply_to, "time": self.now.strftime("%H:%M"),
            "date": timeparse.fmt_dt(self.now, TZ).split("）")[0] + "）",
            "buttons": [b.__dict__ for b in (buttons or [])], "ping": ping, "ephemeral_for": ephemeral_for,
        })
        return mid

    def _msg(self, mid):
        return next((m for m in self.messages if m["id"] == mid), None)

    def _run(self, actions):
        for a in actions:
            if isinstance(a, Send):
                mid = self._add(BOT_ID, a.text, reply_to=a.reply_to, buttons=a.buttons, ping=a.ping)
                if a.reminder_id:
                    self.core.sent(a.reminder_id, mid)
            elif isinstance(a, Edit):
                m = self._msg(a.message_id)
                if m:
                    if a.strike:
                        m["text"] = f"~~{m['text']}~~"
                    if a.append:
                        m["text"] += "\n" + a.append
                    if a.clear_buttons:
                        m["buttons"] = []
            elif isinstance(a, NeedWeather):
                s = self.store.settings()
                text = weather.format_report(a.county, fake_periods(self.now), s["weather_rain_threshold"], self.now)
                text += "\n-# （測試網頁用的假資料，上線後才會接中央氣象署）"
                self._add(BOT_ID, text, reply_to=a.reply_to)


def target_name(t: str) -> str:
    kind, _, tid = t.partition(":")
    if kind == "r":
        return "@" + ROLES.get(int(tid), tid)
    return "@" + MEMBERS.get(int(tid), User(0, tid)).name


def intent_summary(it, tz) -> dict | None:
    if it is None:
        return None
    names = {"help": "說明", "reminder": "提醒", "timer": "計時", "note": "記住", "weather": "天氣",
             "list": "查詢", "cancel": "取消提醒", "delete_note": "刪除記事", "snooze": "延後", "unknown": "聽不懂"}
    parts = []
    if it.when:
        parts.append(timeparse.fmt_dt(it.when, tz))
    if it.repeat:
        parts.append("🔁" + timeparse.describe_repeat(it.repeat))
    if it.seconds:
        parts.append(f"{it.seconds // 60} 分 {it.seconds % 60} 秒" if it.seconds % 60 else f"{it.seconds // 60} 分鐘")
    if it.category:
        parts.append(f"分類：{it.category}")
    if it.scope:
        parts.append({"reminders": "提醒", "notes": "記事", "all": "全部"}.get(it.scope, it.scope))
    if it.ids:
        parts.append("編號 " + "、".join(map(str, it.ids)))
    if it.county:
        parts.append(it.county)
    if it.text and it.kind != "snooze":
        parts.append(f"內容「{it.text}」")
    if it.error:
        parts.append(f"⚠️ {it.error}")
    return {"kind": it.kind, "label": names.get(it.kind, it.kind), "detail": "｜".join(parts)}


def fake_periods(now: datetime) -> list:
    """假的 36 小時預報，時段跟氣象署一樣切（06／18 點）。"""
    local = now.astimezone(TZ)
    day = local.replace(minute=0, second=0, microsecond=0)
    if 6 <= local.hour < 18:
        start = day.replace(hour=6)
    elif local.hour >= 18:
        start = day.replace(hour=18)
    else:
        start = (day - timedelta(days=1)).replace(hour=18)
    samples = [("多雲短暫陣雨", 60, 21, 26, "舒適"), ("多雲", 20, 19, 23, "稍有寒意至舒適"),
               ("晴時多雲", 10, 20, 27, "舒適")]
    out = []
    for i, (wx, pop, lo, hi, ci) in enumerate(samples):
        s = start + timedelta(hours=12 * i)
        out.append(weather.Period(start=s, end=s + timedelta(hours=12), wx=wx, pop=pop, min_t=lo, max_t=hi, ci=ci))
    return out
