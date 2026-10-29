"""Discord bot：收 @mention、分派功能、按鈕、排程。"""
from __future__ import annotations

import logging
import re
import time as _time
from datetime import datetime, time, timedelta

import discord
from discord.ext import tasks

from . import timeparse, weather
from .config import Config
from .db import DB
from .router import Intent, route

log = logging.getLogger(__name__)

PINGS = discord.AllowedMentions(everyone=False, users=True, roles=True, replied_user=False)
NO_PINGS = discord.AllowedMentions.none()

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


def mention_str(target: str) -> str:
    kind, _, tid = target.partition(":")
    return f"<@&{tid}>" if kind == "r" else f"<@{tid}>"


class SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def fill(template: str, **values) -> str:
    return template.format_map(SafeDict(values)).strip()


def can_touch(rem: dict, user: discord.abc.User) -> bool:
    if rem["creator_id"] == user.id or f"u:{user.id}" in rem["targets"]:
        return True
    roles = {r.id for r in getattr(user, "roles", [])}
    return any(t.startswith("r:") and int(t[2:]) in roles for t in rem["targets"])


# ---------- 按鈕（custom_id 帶提醒編號，bot 重開後按鈕還能用） ----------

class DoneButton(discord.ui.DynamicItem[discord.ui.Button], template=r"rem:done:(?P<id>\d+)"):
    def __init__(self, rid: int):
        super().__init__(discord.ui.Button(label="完成", emoji="✅", style=discord.ButtonStyle.success,
                                           custom_id=f"rem:done:{rid}"))
        self.rid = rid

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction):
        await interaction.client.on_done(interaction, self.rid)


class SnoozeButton(discord.ui.DynamicItem[discord.ui.Button],
                   template=r"rem:snooze:(?P<id>\d+):(?P<arg>\d+|tmr)"):
    def __init__(self, rid: int, arg: str):
        label = "明天" if arg == "tmr" else _fmt_minutes(int(arg))
        super().__init__(discord.ui.Button(label=label, emoji="💤", style=discord.ButtonStyle.secondary,
                                           custom_id=f"rem:snooze:{rid}:{arg}"))
        self.rid, self.arg = rid, arg

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["id"]), match["arg"])

    async def callback(self, interaction: discord.Interaction):
        await interaction.client.on_snooze(interaction, self.rid, self.arg)


class CancelButton(discord.ui.DynamicItem[discord.ui.Button], template=r"rem:cancel:(?P<id>\d+)"):
    def __init__(self, rid: int):
        super().__init__(discord.ui.Button(label="取消這個提醒", style=discord.ButtonStyle.secondary,
                                           custom_id=f"rem:cancel:{rid}"))
        self.rid = rid

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction):
        await interaction.client.on_cancel_button(interaction, self.rid)


def _fmt_minutes(m: int) -> str:
    if m % 1440 == 0:
        return f"{m // 1440} 天後"
    if m % 60 == 0:
        return f"{m // 60} 小時後"
    return f"{m} 分鐘後"


def _fmt_seconds(s: int) -> str:
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    parts = [f"{h} 小時" if h else "", f"{m} 分鐘" if m else "", f"{sec} 秒" if sec else ""]
    return " ".join(p for p in parts if p) or "0 秒"


def fired_view(rid: int, settings: dict) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(DoneButton(rid))
    for m in str(settings["snooze_minutes"]).split(","):
        if m.strip().isdigit():
            view.add_item(SnoozeButton(rid, m.strip()))
    if settings["snooze_tomorrow"]:
        view.add_item(SnoozeButton(rid, "tmr"))
    return view


def confirm_view(rid: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(CancelButton(rid))
    return view


# ---------- Bot ----------

class ReminderBot(discord.Client):
    def __init__(self, config: Config):
        intents = discord.Intents.default()   # @bot 的訊息不需要 Message Content 權限也拿得到內容
        super().__init__(intents=intents, allowed_mentions=NO_PINGS)
        self.config = config
        self.tz = config.tz
        self.db = DB(config.db_path)
        self.web_runner = None

    def now(self) -> datetime:
        return datetime.now(self.tz)

    async def setup_hook(self):
        await self.db.open()
        self.add_dynamic_items(DoneButton, SnoozeButton, CancelButton)
        from .web import start_web
        self.web_runner = await start_web(self)
        self.tick.start()

    async def close(self):
        self.tick.cancel()
        if self.web_runner:
            await self.web_runner.cleanup()
        await self.db.close()
        await super().close()

    async def on_ready(self):
        log.info("已登入：%s，服務頻道：%s", self.user, self.config.channel_ids)

    # ---------- 收訊息 ----------
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.channel.id not in self.config.channel_ids:
            return
        # 沒開 Message Content 權限時，只有 @到 bot 的訊息才拿得到內容，所以一定要 @
        if self.user not in message.mentions and not _role_mentions_me(message, self.user):
            return
        replied = await self._replied_bot_message(message)

        text = re.sub(r"<@[!&]?\d+>", " ", message.content)
        settings = await self.db.settings()
        intent = route(text, self.now(), settings)
        if replied and intent.kind in ("snooze", "unknown", "reminder") and re.search(r"延後|晚點|等等|等一下", text):
            intent.kind = "snooze"
        try:
            await self.handle(message, intent, settings, replied)
        except Exception:
            log.exception("處理訊息失敗：%s", message.content)
            await message.reply("😵 出了點問題，晚點再試一次。", mention_author=False)

    async def _replied_bot_message(self, message: discord.Message) -> discord.Message | None:
        ref = message.reference
        if not ref or not ref.message_id:
            return None
        msg = ref.resolved if isinstance(ref.resolved, discord.Message) else None
        if msg is None:
            try:
                msg = await message.channel.fetch_message(ref.message_id)
            except discord.HTTPException:
                return None
        return msg if msg.author.id == self.user.id else None

    async def handle(self, message: discord.Message, it: Intent, s: dict, replied):
        reply = lambda text, **kw: message.reply(text[:2000], mention_author=False, **kw)  # noqa: E731

        if it.error:
            return await reply(f"🤔 {it.error}")

        if it.kind == "help":
            return await reply(HELP)

        if it.kind == "reminder":
            if not s["feature_reminder"]:
                return await reply("提醒功能目前關閉中。")
            return await self.create_reminder(message, it, s)

        if it.kind == "timer":
            if not s["feature_timer"]:
                return await reply("計時功能目前關閉中。")
            due = self.now() + timedelta(seconds=it.seconds)
            rid = await self.db.add_reminder(
                kind="timer", channel_id=message.channel.id, creator_id=message.author.id,
                targets=[f"u:{message.author.id}"], text=it.text, due_at=int(due.timestamp()),
                extra={"duration": _fmt_seconds(it.seconds)})
            return await reply(f"⏱️ 開始計時 {_fmt_seconds(it.seconds)}（#{rid}），"
                               f"{due:%H:%M:%S} 叫你。{('「' + it.text + '」') if it.text else ''}",
                               view=confirm_view(rid))

        if it.kind == "note":
            if not s["feature_note"]:
                return await reply("記事功能目前關閉中。")
            nid = await self.db.add_note(message.author.id, it.category, it.text)
            return await reply(fill(s["note_saved_template"], id=nid, category=it.category, text=it.text))

        if it.kind == "list":
            return await reply(await self.list_text(message.author.id, it.scope, it.category))

        if it.kind == "cancel":
            done, denied = [], []
            for rid in it.ids:
                rem = await self.db.get_reminder(rid)
                if not rem or rem["status"] in ("done", "cancelled"):
                    denied.append(f"#{rid}（找不到）")
                elif not can_touch(rem, message.author):
                    denied.append(f"#{rid}（不是你的）")
                else:
                    await self.db.update_reminder(rid, status="cancelled")
                    done.append(f"#{rid}「{rem['text'] or '提醒'}」")
            out = []
            if done:
                out.append("🗑️ 已取消 " + "、".join(done))
            if denied:
                out.append("⚠️ 沒辦法取消 " + "、".join(denied))
            return await reply("\n".join(out))

        if it.kind == "delete_note":
            ok = [i for i in it.ids if await self.db.delete_note(i, message.author.id)]
            bad = [i for i in it.ids if i not in ok]
            out = ["🗑️ 已刪除記事 " + "、".join(f"#{i}" for i in ok)] if ok else []
            if bad:
                out.append("⚠️ 找不到你的記事 " + "、".join(f"#{i}" for i in bad))
            return await reply("\n".join(out))

        if it.kind == "snooze":
            if not replied:
                return await reply("要延後哪個提醒？請「回覆」那則提醒訊息再說「延後 30 分鐘」，或直接按按鈕。")
            rem = await self.db.reminder_by_message(replied.id)
            if not rem:
                return await reply("這則訊息不是提醒喔。")
            if not can_touch(rem, message.author):
                return await reply("這不是你的提醒喔。")
            if it.text == "tomorrow":
                arg = "tmr"
            else:
                arg = str(max(1, (it.seconds or 600) // 60))
            new_due = await self.snooze(rem, arg, message.author)
            return await reply(f"💤 好，{timeparse.fmt_dt(new_due, self.tz)} 再提醒。")

        if it.kind == "weather":
            if not s["feature_weather"]:
                return await reply("天氣功能目前關閉中。")
            county = it.county or s["weather_county"]
            try:
                periods = await weather.fetch(self.config.cwa_api_key, county, self.tz)
                return await reply(weather.format_report(county, periods, s["weather_rain_threshold"]))
            except weather.WeatherError as e:
                return await reply(f"🌧️ 天氣查不到：{e}")

        await reply("🤔 我聽不太懂，試試「明天下午3點提醒我…」「記住…」「計時10分鐘」，或說「說明」看全部功能。")

    async def create_reminder(self, message: discord.Message, it: Intent, s: dict):
        targets = []
        if s["feature_mention_others"]:
            targets += [f"u:{u.id}" for u in message.mentions if u.id != self.user.id and not u.bot]
            targets += [f"r:{r.id}" for r in message.role_mentions if not _is_my_role(r, self.user)]
        others = bool(targets)
        if not targets:
            targets = [f"u:{message.author.id}"]
        text = it.text or "時間到了！"
        rid = await self.db.add_reminder(
            channel_id=message.channel.id, creator_id=message.author.id, targets=targets,
            text=text, due_at=int(it.when.timestamp()), repeat=it.repeat)
        rep = timeparse.describe_repeat(it.repeat)
        await message.reply(
            fill(s["confirm_template"], id=rid, time=timeparse.fmt_dt(it.when, self.tz),
                 repeat=f"（{rep}）" if rep else "", text=text,
                 targets=(" → 提醒 " + " ".join(mention_str(t) for t in targets)) if others else ""),
            mention_author=False, allowed_mentions=NO_PINGS, view=confirm_view(rid))

    async def list_text(self, user_id: int, scope: str, category: str | None) -> str:
        out = []
        if scope in ("reminders", "all"):
            rems = await self.db.user_reminders(user_id)
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
                    bits.append("→ " + " ".join(mention_str(t) for t in r["targets"]))
                out.append(" ".join(bits))
        if scope in ("notes", "all"):
            notes = await self.db.user_notes(user_id, category)
            title = f"📝 **你的「{category}」**" if category else "📝 **你的記事**"
            out.append(title if notes else f"{title}：還沒有東西。")
            last = None
            for n in notes[:60]:
                if not category and n["category"] != last:
                    out.append(f"__{n['category']}__")
                    last = n["category"]
                out.append(f"`#{n['id']}` {n['text']}")
        return "\n".join(out)

    # ---------- 按鈕 ----------
    async def on_done(self, interaction: discord.Interaction, rid: int):
        rem = await self.db.get_reminder(rid)
        if not rem:
            return await interaction.response.send_message("找不到這個提醒。", ephemeral=True)
        if not can_touch(rem, interaction.user):
            return await interaction.response.send_message("這不是你的提醒喔。", ephemeral=True)
        await self.db.update_reminder(rid, status="done")
        await interaction.response.edit_message(
            content=f"{interaction.message.content}\n-# ✅ {interaction.user.display_name} 已完成", view=None)

    async def on_snooze(self, interaction: discord.Interaction, rid: int, arg: str):
        rem = await self.db.get_reminder(rid)
        if not rem:
            return await interaction.response.send_message("找不到這個提醒。", ephemeral=True)
        if not can_touch(rem, interaction.user):
            return await interaction.response.send_message("這不是你的提醒喔。", ephemeral=True)
        new_due = await self._snooze_update(rem, arg)
        await interaction.response.edit_message(
            content=f"{interaction.message.content}\n-# 💤 已延後到 {timeparse.fmt_dt(new_due, self.tz)}",
            view=None)

    async def on_cancel_button(self, interaction: discord.Interaction, rid: int):
        rem = await self.db.get_reminder(rid)
        if not rem or rem["status"] in ("done", "cancelled"):
            return await interaction.response.send_message("這個提醒已經結束了。", ephemeral=True)
        if not can_touch(rem, interaction.user):
            return await interaction.response.send_message("這不是你的提醒喔。", ephemeral=True)
        await self.db.update_reminder(rid, status="cancelled")
        await interaction.response.edit_message(
            content=f"~~{interaction.message.content}~~\n-# 🗑️ 已取消", view=None)

    async def snooze(self, rem: dict, arg: str, user) -> datetime:
        new_due = await self._snooze_update(rem, arg)
        if rem["message_id"]:
            try:
                ch = self.get_channel(rem["channel_id"]) or await self.fetch_channel(rem["channel_id"])
                msg = await ch.fetch_message(rem["message_id"])
                await msg.edit(content=f"{msg.content}\n-# 💤 已延後到 {timeparse.fmt_dt(new_due, self.tz)}",
                               view=None)
            except discord.HTTPException:
                pass
        return new_due

    async def _snooze_update(self, rem: dict, arg: str) -> datetime:
        s = await self.db.settings()
        now = self.now()
        if arg == "tmr":
            h, m = map(int, s["time_morning"].split(":"))
            new_due = datetime.combine(now.date() + timedelta(days=1), time(h, m), self.tz)
        else:
            new_due = now + timedelta(minutes=int(arg))
        await self.db.update_reminder(rem["id"], status="pending", due_at=int(new_due.timestamp()),
                                      nag_count=0)
        return new_due

    # ---------- 排程 ----------
    @tasks.loop(seconds=15)
    async def tick(self):
        try:
            await self.fire_due()
            await self.nag()
            await self.daily_weather()
        except Exception:
            log.exception("排程執行失敗")

    @tick.before_loop
    async def _before_tick(self):
        await self.wait_until_ready()

    async def _channel(self, channel_id: int):
        return self.get_channel(channel_id) or await self.fetch_channel(channel_id)

    async def fire_due(self):
        now_ts = int(_time.time())
        s = await self.db.settings()
        for rem in await self.db.due_reminders(now_ts):
            try:
                ch = await self._channel(rem["channel_id"])
            except discord.HTTPException:
                log.warning("頻道 %s 找不到，提醒 #%s 取消", rem["channel_id"], rem["id"])
                await self.db.update_reminder(rem["id"], status="cancelled")
                continue
            mentions = " ".join(mention_str(t) for t in rem["targets"])
            if rem["kind"] == "timer":
                content = fill(s["timer_template"], mentions=mentions, text=rem["text"],
                               duration=rem["extra"].get("duration", ""))
            else:
                content = fill(s["reminder_template"], mentions=mentions, text=rem["text"],
                               time=timeparse.fmt_dt(datetime.fromtimestamp(rem["due_at"], self.tz), self.tz),
                               creator=f"<@{rem['creator_id']}>",
                               repeat=timeparse.describe_repeat(rem["repeat"]))

            if rem["repeat"]:
                # 重複提醒：這一次另外存一筆（按鈕、延後都對這筆），本體排到下一次
                occ = await self.db.add_reminder(
                    channel_id=rem["channel_id"], creator_id=rem["creator_id"], targets=rem["targets"],
                    text=rem["text"], due_at=rem["due_at"], parent_id=rem["id"], status="fired")
                msg = await ch.send(content[:2000], view=fired_view(occ, s), allowed_mentions=PINGS)
                await self.db.update_reminder(occ, message_id=msg.id, fired_at=now_ts)
                due = datetime.fromtimestamp(rem["due_at"], self.tz)
                nxt = timeparse.next_occurrence(rem["repeat"], max(self.now(), due),
                                                due.timetz().replace(tzinfo=None), self.tz)
                await self.db.update_reminder(rem["id"], due_at=int(nxt.timestamp()))
            else:
                msg = await ch.send(content[:2000], view=fired_view(rem["id"], s), allowed_mentions=PINGS)
                await self.db.update_reminder(rem["id"], status="fired", message_id=msg.id,
                                              fired_at=now_ts, nag_count=0)

    async def nag(self):
        s = await self.db.settings()
        if not s["nag_enabled"] or s["nag_max"] <= 0:
            return
        before = int(_time.time()) - s["nag_interval_minutes"] * 60
        for rem in await self.db.nag_candidates(before, s["nag_max"]):
            try:
                ch = await self._channel(rem["channel_id"])
                if rem["message_id"]:
                    try:
                        old = await ch.fetch_message(rem["message_id"])
                        await old.edit(view=None)
                    except discord.HTTPException:
                        pass
                content = fill(s["nag_template"], mentions=" ".join(mention_str(t) for t in rem["targets"]),
                               text=rem["text"], count=rem["nag_count"] + 2)
                msg = await ch.send(content[:2000], view=fired_view(rem["id"], s), allowed_mentions=PINGS)
                await self.db.update_reminder(rem["id"], message_id=msg.id, fired_at=int(_time.time()),
                                              nag_count=rem["nag_count"] + 1)
            except discord.HTTPException:
                log.exception("再提醒 #%s 失敗", rem["id"])

    async def daily_weather(self, force: bool = False):
        s = await self.db.settings()
        if not (s["feature_weather"] and (s["weather_daily_enabled"] or force)):
            return
        now = self.now()
        h, m = map(int, s["weather_daily_time"].split(":"))
        target = datetime.combine(now.date(), time(h, m), self.tz)
        if not force:
            if not (target <= now < target + timedelta(minutes=30)):
                return
            if await self.db.get_meta("weather_sent") == now.date().isoformat():
                return
            await self.db.set_meta("weather_sent", now.date().isoformat())
        ch = await self._channel(min(self.config.channel_ids))
        county = s["weather_county"]
        try:
            periods = await weather.fetch(self.config.cwa_api_key, county, self.tz)
            await ch.send(weather.format_report(county, periods, s["weather_rain_threshold"]))
        except weather.WeatherError as e:
            log.warning("每日天氣失敗：%s", e)
            if force:
                raise


def _is_my_role(role: discord.Role, me) -> bool:
    return role.is_bot_managed() and role.tags is not None and role.tags.bot_id == me.id


def _role_mentions_me(message: discord.Message, me) -> bool:
    return any(_is_my_role(r, me) for r in message.role_mentions)
