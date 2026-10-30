"""測試網頁用：在瀏覽器（Pyodide）裡模擬一個頻道，核心邏輯和正式 bot 完全同一份。

JS 只負責畫面；所有判斷都在這裡呼叫 reminderbot.core。每個方法回傳 JSON 字串。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from reminderbot import rules, timeparse, weather
from reminderbot.explain import describe, rules as logic_rules
from reminderbot.core import Assistant, Edit, Incoming, NeedWeather, React, Send, User
from reminderbot.settings import (COUNTIES, DEFAULTS, GROUPS, HINTS, LABELS, MULTILINE_KEYS, REPLY_GROUPS, REPLY_KEYS,
                                  SETTING_KEYS, note_categories, validate, wish_lists)
from reminderbot.store import MemoryStore

TZ = timezone(timedelta(hours=8))
BOT_ID = 1000
CHANNEL = 1                       # 提醒頻道
CHANNELS = {1: "提醒", 2: "閒聊"}
MEMBERS = {
    1: User(1, "你", {50}),
    2: User(2, "小明", {50}),
    3: User(3, "小美", set()),
}
ROLES = {50: "樂團"}


class Sim:
    def __init__(self, start_ms: float):
        self.store = MemoryStore()
        self.core = Assistant(self.store, TZ, owner_id=1, home_channel_id=CHANNEL)     # 「你」是 bot 的主人
        self.now = datetime.fromtimestamp(start_ms / 1000, TZ).replace(second=0, microsecond=0)
        self.messages: list[dict] = []
        self.fake_rain: int | None = None      # 假預報：幾點開始下雨（None = 不下雨）
        self._eph: int | None = None           # / 指令的回覆只給這個人看
        self.next_id = 1
        self.store.save_settings({"weather_county": "臺北市"})
        # 示範一條自訂規則（在「規則」頁可以改）
        demo, _ = rules.from_form({"name": "催作業", "on": "on", "who": "others", "call": "no", "words": "作業/報告",
                                   "not_words": "交了/寫完", "replies": "{名字}快去寫\n還不寫？", "cooldown": "10"}, 1)
        rules.save(self.store, [demo])
        self.store.set_meta("rule_seq", 1)

    # ---------- 給 JS 的方法 ----------
    def info(self) -> str:
        return json.dumps({
            "members": [{"id": u.id, "name": u.name, "roles": sorted(u.role_ids)} for u in MEMBERS.values()],
            "roles": [{"id": k, "name": v} for k, v in ROLES.items()],
            "bot": {"id": BOT_ID, "name": "jvs"},
            "channels": [{"id": k, "name": v} for k, v in CHANNELS.items()],
            "defaults": DEFAULTS, "labels": LABELS, "hints": HINTS, "groups": GROUPS, "reply_groups": REPLY_GROUPS, "counties": COUNTIES,
            "multiline": MULTILINE_KEYS,
            "rule_choices": {"who": rules.WHO, "call": rules.CALL, "task": rules.TASK, "mode": rules.MODE},
            "rule_blank": rules.BLANK,
        }, ensure_ascii=False)

    def say(self, user_id: int, text: str, reply_to: int | None = None, tag: str = "", mentioned: bool = True,
            channel: int = CHANNEL) -> str:
        channel = int(channel)
        if reply_to and self._msg(reply_to):
            channel = self._msg(reply_to)["channel"]     # 回覆一定在那則訊息的頻道
        user = MEMBERS[int(user_id)]
        reply_bot = reply_to if reply_to and self._msg(reply_to) and self._msg(reply_to)["author"] == BOT_ID else None
        # 回覆 bot 的訊息就算在跟 bot 講話，文字裡不會有 @（跟 Discord 一樣）
        raw, mentions, roles = self._mentions(text, mentioned and not reply_bot)
        mid = self._add(author=user.id, text=raw, reply_to=reply_to, channel=channel)
        if tag:
            self._msg(mid)["tag"] = tag
        mentioned = f"<@{BOT_ID}>" in raw or reply_bot is not None
        actions = self.core.handle(Incoming(user, raw, channel, mid, mentions, roles, reply_bot, mentioned), self.now)
        self._msg(mid)["intent"] = describe(self.core.last_intent, TZ)
        self._run(actions)
        return self.state()

    def press(self, user_id: int, message_id: int, custom_id: str) -> str:
        user = MEMBERS[int(user_id)]
        msg = self._msg(int(message_id))
        res = self.core.press(custom_id, user, self.now, msg["channel"] if msg else CHANNEL)
        if res.edit_menu:
            return self.state(note="__edit__")      # 測試台：切到「提醒」分頁編輯
        if res.ephemeral:
            self._add(author=BOT_ID, text=res.ephemeral, ephemeral_for=user.id, channel=msg["channel"] if msg else CHANNEL)
        elif msg:
            if res.strike:
                msg["text"] = f"~~{msg['text']}~~"
            if res.append:
                msg["text"] += "\n" + res.append
            msg["buttons"] = []
        self._run(res.actions)
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

    def save_settings(self, form_json: str, page: str = "settings") -> str:
        values, errors = validate(json.loads(form_json), REPLY_KEYS if page == "replies" else SETTING_KEYS)
        self.store.save_settings(values)
        return self.state(note="；".join(errors) if errors else "已套用")

    def reset_settings(self, page: str = "settings") -> str:
        self.store.save_settings({k: DEFAULTS[k] for k in (REPLY_KEYS if page == "replies" else SETTING_KEYS)})
        return self.state(note="已恢復預設")

    def slash(self, user_id: int, name: str, arg: str = "", channel: int = CHANNEL) -> str:
        """/說明、/設定、/天氣：回覆只有本人看得到。"""
        cmd = {"說明": "help", "設定": "settings", "天氣": "weather", "提醒": "reminders",
               "stark says": "stark"}.get(name.strip("/ ").lower(), name)
        # Stark says：回頻道裡最新一則別人的訊息
        target = next((m["id"] for m in reversed(self.messages) if m["channel"] == int(channel)
                       and m["author"] not in (0, BOT_ID, int(user_id))), None)
        self._eph = int(user_id)
        try:
            self._run(self.core.slash(cmd, MEMBERS[int(user_id)], self.now, int(channel), arg,
                                      settings_url="https://158-179-177-8.sslip.io", target_id=target))
        finally:
            self._eph = None
        return self.state()

    def rem_edit(self, user_id: int, rid: int, when_text: str, text: str) -> str:
        ok, msg = self.core.edit_reminder(int(rid), MEMBERS[int(user_id)], self.now, when_text, text)
        return self.state(note=msg)

    def rem_cancel(self, user_id: int, rid: int) -> str:
        self.core.press(f"rem:cancel:{int(rid)}", MEMBERS[int(user_id)], self.now)
        return self.state(note="已取消")

    def set_rain(self, hour: int) -> str:
        """改假預報：今天幾點開始下雨（-1 = 不下雨）。下一次檢查（每 40 分鐘）就會發現變化。"""
        self.fake_rain = None if int(hour) < 0 else int(hour)
        return self.state(note="已改預報")

    def divider(self, text: str) -> str:
        """批次測試的分段標題（不是訊息，bot 不會回）。"""
        self._add(author=0, text=text)
        return self.state()

    def rule_save(self, form_json: str, rid: int = 0) -> str:
        all_rules = rules.load(self.store)
        rid = int(rid) or max([int(self.store.get_meta("rule_seq") or 0)] + [r["id"] for r in all_rules]) + 1
        rule, errors = rules.from_form(json.loads(form_json), rid)
        if errors:
            return self.state("沒存到：" + "；".join(errors))
        if any(r["id"] == rid for r in all_rules):
            all_rules = [rule if r["id"] == rid else r for r in all_rules]
        else:
            all_rules.append(rule)
            self.store.set_meta("rule_seq", rid)
        rules.save(self.store, all_rules)
        return self.state("已儲存")

    def rule_delete(self, rid: int) -> str:
        rules.save(self.store, [r for r in rules.load(self.store) if r["id"] != int(rid)])
        self.core.set_images(f"r{int(rid)}", [])
        return self.state("已刪除")

    def rule_up(self, rid: int) -> str:
        all_rules = rules.load(self.store)
        i = next((i for i, r in enumerate(all_rules) if r["id"] == int(rid)), 0)
        if i:
            all_rules[i - 1], all_rules[i] = all_rules[i], all_rules[i - 1]
            rules.save(self.store, all_rules)
        return self.state()

    def set_images(self, group: str, names_json: str) -> str:
        """測試台的圖庫：圖片本身留在瀏覽器，這裡只記檔名。"""
        self.core.set_images(group, json.loads(names_json))
        return self.state()

    def restart(self, start_ms: float) -> str:
        """清空並把時間設到指定時刻（批次測試要固定起點，結果才能重現）。自訂規則保留。"""
        keep = {k: self.store.get_meta(k) for k in ("rules", "rule_seq")}
        self.__init__(start_ms)
        for k, v in keep.items():
            self.store.set_meta(k, v)
        return self.state()

    def logic(self) -> str:
        return json.dumps(logic_rules(self.store.settings()), ensure_ascii=False)

    def explain(self, text: str) -> str:
        from reminderbot.router import route
        return json.dumps(describe(route(text, self.now, self.store.settings()), TZ), ensure_ascii=False)

    def note_update(self, note_id: int, category: str, text: str) -> str:
        if text.strip():
            self.store.update_note(int(note_id), category=category.strip(), text=text.strip())
        return self.state()

    def note_delete(self, note_id: int) -> str:
        self.store.delete_note(int(note_id))
        return self.state()

    def note_add(self, category: str, text: str) -> str:
        if text.strip():
            self.store.add_note(1, category.strip(), text.strip(), self.now.timestamp())
        return self.state()

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
            "notes": [{"id": n["id"], "category": n["category"], "text": n["text"], "mentions": n.get("mentions") or 1,
                       "ago": self.core.ago(n, self.now), "credit": round(float(n.get("credit") or 0), 2),
                       "user": MEMBERS.get(n["user_id"], User(0, "?")).name} for n in self.store.all_notes()],
            "lists": [n for n, _ in wish_lists(self.store.settings())] +
                     [n for n, _ in note_categories(self.store.settings())],
            "settings": self.store.settings(),
            "rules": [{**r, "summary": rules.summary(r)} for r in rules.load(self.store)],
            "imgcodes": {ref: code for code, ref in self.core.image_codes().items()},
            "next_event": (lambda t: timeparse.fmt_dt(t, TZ) if t else None)(self.core.next_event_at(self.now)),
            "note": note,
            "fake_rain": self.fake_rain,
        }, ensure_ascii=False)

    # ---------- 內部 ----------
    def _mentions(self, text: str, mentioned: bool = True):
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
        if mentioned and f"<@{BOT_ID}>" not in raw:
            raw = f"<@{BOT_ID}> {raw}"          # 預設每句都當作有 @jvs；關掉「@jvs」就是一般聊天
        return raw, mentions, roles

    def _add(self, author: int, text: str, reply_to=None, buttons=None, ping=False, ephemeral_for=None,
             channel: int = CHANNEL) -> int:
        mid = self.next_id
        self.next_id += 1
        self.messages.append({
            "id": mid, "author": author, "text": text, "reply_to": reply_to, "time": self.now.strftime("%H:%M"),
            "date": timeparse.fmt_dt(self.now, TZ).rsplit(" ", 1)[0],
            "buttons": [b.__dict__ for b in (buttons or [])], "ping": ping, "ephemeral_for": ephemeral_for,
            "channel": channel,
        })
        return mid

    def _msg(self, mid):
        return next((m for m in self.messages if m["id"] == mid), None)

    def _run(self, actions):
        for a in actions:
            if isinstance(a, Send):
                mid = self._add(BOT_ID, a.text, reply_to=a.reply_to, buttons=a.buttons, ping=a.ping,
                                channel=a.channel_id or CHANNEL, ephemeral_for=self._eph if a.ephemeral else None)
                if a.embed:
                    self._msg(mid)["embed"] = a.embed
                if a.image:
                    self._msg(mid)["image"] = self.core.resolve_image(a.image)
                if a.reminder_id:
                    self.core.sent(a.reminder_id, mid, a.channel_id)
            elif isinstance(a, React):
                m = self._msg(a.message_id)
                if m is not None and a.emoji not in m.setdefault("reactions", []):
                    m["reactions"].append(a.emoji)
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
                b = weather.demo_bundle(self.now, a.county, a.town, rain_from=self.fake_rain, week=a.need_week)
                self._run(self.core.weather_result(a, b, self.now))

def target_name(t: str) -> str:
    kind, _, tid = t.partition(":")
    if kind == "r":
        return "@" + ROLES.get(int(tid), tid)
    return "@" + MEMBERS.get(int(tid), User(0, tid)).name
