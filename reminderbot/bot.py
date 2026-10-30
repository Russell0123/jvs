"""Discord 轉接層：把 Discord 的訊息／按鈕轉給 core.Assistant，再把它回傳的動作做出來。"""
from __future__ import annotations

import asyncio
import io
import logging
import re
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import tasks

from . import images, weather
from .config import Config
from .core import Assistant, Button, Edit, Incoming, NeedWeather, Purge, React, Send, User
from .store import SqliteStore

log = logging.getLogger(__name__)

PINGS = discord.AllowedMentions(everyone=False, users=True, roles=True, replied_user=False)
NO_PINGS = discord.AllowedMentions.none()
# 說法裡的 :duzi: → <:duzi:ID>（bot 送 :名字: 不會自己變表情）；<:x:1>、<a:x:1>、12:30:00 不會被動到
EMOJI_RE = re.compile(r"(?<![<\w])[:：]([A-Za-z0-9_]{2,32})[:：](?!\d)")
STYLES = {"success": discord.ButtonStyle.success, "danger": discord.ButtonStyle.danger,
          "primary": discord.ButtonStyle.primary,
          "secondary": discord.ButtonStyle.secondary}


class CoreButton(discord.ui.DynamicItem[discord.ui.Button], template=r"rem:[a-z]+:\d+(?::[0-9a-z]+)?"):
    """所有提醒按鈕共用：custom_id 原封不動交給 core.press，bot 重開後舊按鈕也能按。"""

    def __init__(self, custom_id: str, label: str = "", emoji: str = "", style: str = "secondary"):
        super().__init__(discord.ui.Button(label=label or None, emoji=emoji or None,
                                           style=STYLES.get(style, discord.ButtonStyle.secondary),
                                           custom_id=custom_id))
        self.cid = custom_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(item.custom_id)

    async def callback(self, interaction: discord.Interaction):
        await interaction.client.on_press(interaction, self.cid)


def make_view(buttons: list[Button]) -> discord.ui.View | None:
    if not buttons:
        return None
    view = discord.ui.View(timeout=None)
    for b in [b for b in buttons if b.url]:
        view.add_item(discord.ui.Button(label=b.label, url=b.url))
    buttons = [b for b in buttons if not b.url]
    for b in buttons:
        view.add_item(CoreButton(b.custom_id, b.label, b.emoji, b.style))
    return view


class EditMenu(discord.ui.View):
    """只有本人看得到的編輯選單（不用存，10 分鐘後失效）。"""

    def __init__(self, bot: "ReminderBot", rems: list[dict]):
        super().__init__(timeout=600)
        self.bot = bot
        options = [discord.SelectOption(label=f"#{r['id']} {r['text'] or '提醒'}"[:100],
                                        description=self.bot.core.when_label(r)[:100], value=str(r["id"]))
                   for r in rems[:25]]
        select = discord.ui.Select(placeholder="選一個提醒", options=options)
        select.callback = self.chosen
        self.select = select
        self.add_item(select)

    async def chosen(self, interaction: discord.Interaction):
        rid = int(self.select.values[0])
        rem = self.bot.store.get_reminder(rid)
        line = f"`#{rid}` {self.bot.core.when_label(rem)}・{rem['text'] or '提醒'}"
        await interaction.response.edit_message(content=line, view=EditActions(self.bot, rid))


class EditActions(discord.ui.View):
    def __init__(self, bot: "ReminderBot", rid: int):
        super().__init__(timeout=600)
        self.bot, self.rid = bot, rid

    @discord.ui.button(label="改時間", style=discord.ButtonStyle.primary)
    async def when(self, interaction: discord.Interaction, _):
        await interaction.response.send_modal(EditModal(self.bot, self.rid, "when"))

    @discord.ui.button(label="改內容")
    async def text(self, interaction: discord.Interaction, _):
        await interaction.response.send_modal(EditModal(self.bot, self.rid, "text"))

    @discord.ui.button(label="取消提醒", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, _):
        res = self.bot.core.press(f"rem:cancel:{self.rid}", to_user(interaction.user), self.bot.now())
        await interaction.response.edit_message(content=res.ephemeral or res.append.removeprefix("-# "), view=None)


class EditModal(discord.ui.Modal):
    def __init__(self, bot: "ReminderBot", rid: int, what: str):
        super().__init__(title="改時間" if what == "when" else "改內容")
        self.bot, self.rid, self.what = bot, rid, what
        rem = bot.store.get_reminder(rid)
        self.field = discord.ui.TextInput(
            label="什麼時候？" if what == "when" else "提醒內容",
            placeholder="明天下午3點、10分鐘後、每天早上8點" if what == "when" else "",
            default=None if what == "when" else (rem["text"] if rem else ""), max_length=100)
        self.add_item(self.field)

    async def on_submit(self, interaction: discord.Interaction):
        value = str(self.field.value)
        ok, msg = self.bot.core.edit_reminder(self.rid, to_user(interaction.user), self.bot.now(),
                                              when_text=value if self.what == "when" else "",
                                              new_text=value if self.what == "text" else "")
        rem = self.bot.store.get_reminder(self.rid)
        detail = f"\n`#{self.rid}` {self.bot.core.when_label(rem)}・{rem['text'] or '提醒'}" if ok and rem else ""
        await interaction.response.send_message(msg + detail, ephemeral=True)


def make_embed(card: dict) -> discord.Embed:
    e = discord.Embed(title=card.get("title"), description=card.get("description") or None,
                      color=card.get("color"))
    for f in card.get("fields", []):
        e.add_field(name=f["name"], value=f["value"] or "—", inline=f.get("inline", True))
    if card.get("footer"):
        e.set_footer(text=card["footer"])
    return e


def to_user(member: discord.abc.User) -> User:
    return User(member.id, member.display_name, {r.id for r in getattr(member, "roles", [])})


class ReminderBot(discord.Client):
    def __init__(self, config: Config):
        intents = discord.Intents.default()
        # 沒 @ bot 的訊息也要讀（記錄「我想畫…」），需要在 Developer Portal 打開 Message Content Intent
        intents.message_content = True
        super().__init__(intents=intents, allowed_mentions=NO_PINGS)
        self.config = config
        self.tz = config.tz
        self.store = SqliteStore(config.db_path)
        self.core = Assistant(self.store, self.tz, owner_id=config.owner_id,
                              home_channel_id=config.reminder_channel_id)
        self.web_runner = None
        self._tasks: set = set()         # 背景的天氣查詢
        self._monitoring = False         # 天氣變化檢查還在跑就不重複開
        self._purging: set[int] = set()  # 正在刪訊息的頻道

    def now(self) -> datetime:
        return datetime.now(self.tz)

    async def setup_hook(self):
        self.add_dynamic_items(CoreButton)
        self._setup_commands()
        from .web import start_web
        self.web_runner = await start_web(self)
        self.tick.start()

    async def close(self):
        self.tick.cancel()
        if self.web_runner:
            await self.web_runner.cleanup()
        self.store.close()
        await super().close()

    async def on_ready(self):
        log.info("已登入：%s，服務頻道：%s", self.user, self.config.channel_ids)
        # / 指令註冊到每個伺服器（全域註冊要等很久才生效）
        for guild in self.guilds:
            try:
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
            except discord.HTTPException as e:
                log.warning("/ 指令註冊失敗（%s）：%s。重新邀請 bot 時要勾 applications.commands", guild.name, e)

    # ---------- / 指令 ----------
    def _setup_commands(self):
        self.tree = app_commands.CommandTree(self)

        @self.tree.command(name="說明", description="看賈維斯會做什麼")
        async def help_cmd(interaction: discord.Interaction):
            await self._slash(interaction, "help")

        @self.tree.command(name="設定", description="打開設定網頁")
        async def settings_cmd(interaction: discord.Interaction):
            await self._slash(interaction, "settings")

        @self.tree.command(name="提醒", description="看和修改你的提醒")
        async def reminders_cmd(interaction: discord.Interaction):
            await self._slash(interaction, "reminders")

        stark = app_commands.Group(name="stark", description="Stark 語錄")

        @stark.command(name="says", description="賈維斯拿 Stark 的圖回覆某則訊息")
        @app_commands.describe(圖片="換一張圖（不放就用預設的）", 訊息="要回的訊息連結（不填就回頻道裡最新一則別人的訊息）",
                               文字="順便說一句")
        async def stark_cmd(interaction: discord.Interaction, 圖片: discord.Attachment | None = None,
                            訊息: str = "", 文字: str = ""):
            await self._stark(interaction, None, 圖片, 訊息, 文字)
        self.tree.add_command(stark)

        @self.tree.context_menu(name="Stark says")
        async def stark_menu(interaction: discord.Interaction, message: discord.Message):
            await self._stark(interaction, message)

        @self.tree.command(name="清除", description="刪掉這個頻道的訊息（只有主人能用，會先問）")
        @app_commands.describe(範圍="例如：全部、近7天、今天、近2小時、最近50則")
        async def purge_cmd(interaction: discord.Interaction, 範圍: str):
            await self._slash(interaction, "purge", 範圍)

        @self.tree.command(name="天氣", description="看天氣（只有你看得到）")
        @app_commands.describe(地點="例如：台中、明天、台中明天（不填就看你所在地）")
        async def weather_cmd(interaction: discord.Interaction, 地點: str = ""):
            await self._slash(interaction, "weather", 地點)

    async def _stark(self, interaction: discord.Interaction, target: discord.Message | None,
                     upload: discord.Attachment | None = None, link: str = "", text: str = ""):
        """右鍵選單直接拿到那則訊息；/stark says 用連結找，沒給就找頻道裡最新一則別人的訊息。"""
        if upload and not (upload.content_type or "").startswith("image/"):
            await interaction.response.send_message("要放圖片喔。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)     # 找訊息、下載圖可能超過 3 秒
        channel = interaction.channel
        if target is None and link.strip():
            ids = re.findall(r"\d{15,}", link)
            try:
                ch = await self._channel(int(ids[-2])) if len(ids) >= 2 else channel
                target = await ch.fetch_message(int(ids[-1])) if ids else None
            except (discord.HTTPException, ValueError):
                target = None
        if target is None and not link.strip() and channel is not None:
            async for m in channel.history(limit=30):
                if m.author.id not in (interaction.user.id, self.user.id) and m.type == discord.MessageType.default:
                    target = m
                    break
        actions = self.core.slash("stark", to_user(interaction.user), self.now(),
                                  target.channel.id if target else interaction.channel_id or 0, text,
                                  target_id=target.id if target else None)
        for a in actions:
            if a.ephemeral:
                await interaction.followup.send(self.emo(a.text, interaction.guild), ephemeral=True)
                continue
            file = discord.File(io.BytesIO(await upload.read()), filename=upload.filename) if upload else None
            if file is None and (p := images.path_of(images.folder(self.config.db_path), self.core.resolve_image(a.image))):
                file = discord.File(p)
            kwargs = {"file": file} if file else {}
            await target.reply(self.emo(a.text, target.guild) or None, mention_author=False, allowed_mentions=NO_PINGS,
                               **kwargs)

    async def _slash(self, interaction: discord.Interaction, name: str, arg: str = ""):
        actions = self.core.slash(name, to_user(interaction.user), self.now(), interaction.channel_id or 0, arg,
                                  self.config.public_url)
        nw = next((a for a in actions if isinstance(a, NeedWeather)), None)
        if nw:
            await interaction.response.defer(ephemeral=True, thinking=True)
            s = self.store.settings()
            try:
                b = await weather.fetch_with_retry(
                    lambda: weather.fetch_bundle(self.config.cwa_api_key, nw.county, nw.town, self.tz,
                                                 need_week=nw.need_week),
                    int(s["weather_retries"]), float(s["weather_retry_wait"]))
                actions = self.core.weather_result(nw, b, self.now())
            except weather.WeatherError as e:
                actions = self.core.weather_error(nw, str(e))
            for a in actions:
                kwargs = {"embed": make_embed(a.embed)} if a.embed else {}
                await interaction.followup.send(self.emo(a.text, interaction.guild) or None, ephemeral=True, **kwargs)
            return
        for a in actions:
            view = make_view(a.buttons)
            kwargs = {"view": view} if view else {}
            await interaction.response.send_message(self.emo(a.text, interaction.guild), ephemeral=True, **kwargs)

    # ---------- 收訊息 ----------
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if message.channel.id not in self.config.channel_ids and not self.store.settings()["all_channels"]:
            return
        # 回覆 bot 的訊息也算在跟 bot 講話（回覆時文字裡不會有 @，但 Discord 會帶上「回覆的是哪則」）
        replied = await self._replied_bot_message_id(message)
        mentioned = self.user in message.mentions or any(self._is_my_role(r) for r in message.role_mentions) \
            or replied is not None
        if not mentioned and not message.content:
            return      # 沒開 Message Content Intent 時拿不到內容
        incoming = Incoming(
            author=to_user(message.author), text=message.content, channel_id=message.channel.id,
            message_id=message.id,
            mention_user_ids=[u.id for u in message.mentions if u.id != self.user.id and not u.bot],
            mention_role_ids=[r.id for r in message.role_mentions if not self._is_my_role(r)],
            reply_to_bot_message_id=replied,
            mentioned=mentioned)
        try:
            await self.run_actions(self.core.handle(incoming, self.now()), source=message)
        except Exception:
            log.exception("處理訊息失敗：%s", message.content)
            await message.reply("😵 出了點問題，晚點再試一次。", mention_author=False)

    def _is_my_role(self, role: discord.Role) -> bool:
        return role.is_bot_managed() and role.tags is not None and role.tags.bot_id == self.user.id

    async def _replied_bot_message_id(self, message: discord.Message) -> int | None:
        ref = message.reference
        if not ref or not ref.message_id:
            return None
        msg = ref.resolved if isinstance(ref.resolved, discord.Message) else None
        if msg is None:
            try:
                msg = await message.channel.fetch_message(ref.message_id)
            except discord.HTTPException:
                return None
        return msg.id if msg.author.id == self.user.id else None

    # ---------- 執行 core 回傳的動作 ----------
    def emo(self, text: str | None, guild: discord.Guild | None = None) -> str | None:
        """把 :名字: 換成伺服器自訂表情；先找這個伺服器的，再找 bot 在的其他伺服器，找不到就原樣。"""
        if not text or ":" not in text:
            return text

        def sub(m):
            name = m.group(1)
            e = (guild and discord.utils.get(guild.emojis, name=name)) or discord.utils.get(self.emojis, name=name)
            return str(e) if e else m.group(0)
        return EMOJI_RE.sub(sub, text)

    async def _react(self, a: React, source: discord.Message | None):
        msg = source if source is not None and source.id == a.message_id else \
            (await self._channel(a.channel_id)).get_partial_message(a.message_id)
        emoji = self.emo(a.emoji, getattr(source, "guild", None))
        if EMOJI_RE.fullmatch(emoji):
            log.warning("找不到表符 %s：名稱要跟伺服器裡的一樣", a.emoji)
            return
        await msg.add_reaction(emoji)

    async def _purge(self, a: Purge, interaction: discord.Interaction):
        """一批 100 則：兩週內的一次刪（bulk），更舊的只能一則一則刪（discord.py 會自己排隊等限速）。"""
        if a.channel_id in self._purging:
            return await interaction.followup.send("這個頻道已經在刪了。", ephemeral=True)
        self._purging.add(a.channel_id)
        ch = await self._channel(a.channel_id)
        confirm = interaction.message
        extra, before = [], None
        if confirm is not None and not confirm.flags.ephemeral:
            # 聊天叫的：從「叫它刪」那句往前算，確認訊息和那句最後另外刪
            ref = confirm.reference.message_id if confirm.reference else None
            before = discord.Object(ref or confirm.id)
            extra = [i for i in (confirm.id, ref) if i]
        progress = None
        try:
            progress = await interaction.followup.send("🧹 刪除中…", ephemeral=True, wait=True)
        except discord.HTTPException:
            pass
        after = datetime.fromtimestamp(a.after, timezone.utc) if a.after else None
        cutoff = discord.utils.utcnow() - timedelta(days=13, hours=23)
        total, batch, last_report = 0, [], 0

        async def flush():
            nonlocal total, batch, last_report
            young = [m for m in batch if m.created_at > cutoff]
            singles = [m for m in batch if m.created_at <= cutoff]
            if len(young) > 1:
                await ch.delete_messages(young)
            else:
                singles += young
            for m in singles:
                try:
                    await m.delete()
                except discord.NotFound:
                    pass
            total += len(batch)
            batch = []
            if progress and total - last_report >= 100:
                last_report = total
                try:
                    await progress.edit(content=f"🧹 刪除中…已刪 {total} 則")
                except discord.HTTPException:
                    pass
        try:
            async for m in ch.history(limit=None, before=before, after=after, oldest_first=False):
                if m.pinned or m.id in extra:
                    continue
                batch.append(m)
                if len(batch) >= 100 or (a.limit and total + len(batch) >= a.limit):
                    await flush()
                    if a.limit and total >= a.limit:
                        break
            if batch:
                await flush()
            for mid in extra:
                try:
                    await ch.get_partial_message(mid).delete()
                except discord.HTTPException:
                    pass
            done = f"🧹 刪好了，{a.label}共 {total} 則。"
        except discord.Forbidden:
            done = f"🧹 刪到一半沒權限了（已刪 {total} 則）。要給賈維斯「管理訊息」權限。"
        except discord.HTTPException as e:
            log.exception("刪訊息失敗")
            done = f"🧹 刪到一半出錯了（已刪 {total} 則）：{e}"
        finally:
            self._purging.discard(a.channel_id)
        try:
            await progress.edit(content=done) if progress else await interaction.followup.send(done, ephemeral=True)
        except discord.HTTPException:
            await ch.send(done, delete_after=15)       # 超過 15 分鐘，按鈕那邊已經不能回了

    async def _channel(self, channel_id: int):
        return self.get_channel(channel_id) or await self.fetch_channel(channel_id)

    async def run_actions(self, actions: list, source: discord.Message | None = None):
        for a in actions:
            try:
                if isinstance(a, Send):
                    await self._send(a, source)
                elif isinstance(a, Edit):
                    ch = await self._channel(a.channel_id)
                    msg = await ch.fetch_message(a.message_id)
                    content = f"~~{msg.content}~~" if a.strike else msg.content
                    if a.append:
                        content = f"{content}\n{self.emo(a.append, msg.guild)}"
                    await msg.edit(content=content, view=None if a.clear_buttons else discord.utils.MISSING)
                elif isinstance(a, NeedWeather):
                    await self._weather(a, source)
                elif isinstance(a, React):
                    await self._react(a, source)
            except discord.HTTPException:
                log.exception("動作失敗：%s", a)

    async def _send(self, a: Send, source: discord.Message | None):
        mentions = PINGS if a.ping else NO_PINGS
        view = make_view(a.buttons)
        kwargs = {"allowed_mentions": mentions}
        if view:
            kwargs["view"] = view
        if a.embed:
            kwargs["embed"] = make_embed(a.embed)
        if a.image:
            if p := images.path_of(images.folder(self.config.db_path), self.core.resolve_image(a.image)):
                kwargs["file"] = discord.File(p)
            else:
                log.warning("圖片檔不見了：%s（到設定網頁的圖片頁重新上傳）", a.image)
        if source is not None and a.reply_to == source.id:
            msg = await source.reply(self.emo(a.text, source.guild) or None, mention_author=False, **kwargs)
        else:
            channel = await self._channel(a.channel_id)
            text = self.emo(a.text, getattr(channel, "guild", None)) or None
            if a.reply_to:
                kwargs["reference"] = discord.MessageReference(message_id=a.reply_to, channel_id=channel.id,
                                                               fail_if_not_exists=False)
            msg = await channel.send(text, **kwargs)
        if a.reminder_id:
            self.core.sent(a.reminder_id, msg.id, msg.channel.id)

    async def _weather(self, a: NeedWeather, source: discord.Message | None, raise_errors: bool = False):
        """抓天氣：失敗先連試幾次，都失敗等幾分鐘再一輪（在背景跑，不會卡住提醒）。"""
        if a.purpose == "monitor":
            if self._monitoring:
                return
            self._monitoring = True
        s = self.store.settings()

        async def fetch():
            return await weather.fetch_bundle(self.config.cwa_api_key, a.county, a.town, self.tz,
                                              need_current=a.need_current, need_week=a.need_week)

        async def on_wait(err):
            log.warning("天氣第一輪重試失敗（%s），%s 分鐘後再試", err, s["weather_retry_wait"])
            for act in self.core.weather_wait(a):
                await self._send(act, source)

        async def job():
            try:
                b = await weather.fetch_with_retry(fetch, int(s["weather_retries"]), float(s["weather_retry_wait"]),
                                                   on_wait=on_wait)
                if b.errors:
                    log.warning("天氣部分資料沒抓到：%s", b.errors)
                actions = self.core.weather_result(a, b, self.now())
            except weather.WeatherError as e:
                if raise_errors:
                    raise
                log.warning("天氣查詢失敗：%s", e)
                actions = self.core.weather_error(a, str(e))
            finally:
                if a.purpose == "monitor":
                    self._monitoring = False
            for act in actions:
                if isinstance(act, Send):
                    await self._send(act, source)

        if raise_errors:
            await job()             # 設定網頁的「貼一次天氣」要等結果
        else:
            task = asyncio.create_task(job())
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def post_weather_now(self):
        """設定網頁的「貼一次天氣」。"""
        county, town = self.core.location(self.config.owner_id, self.store.settings())
        await self._weather(NeedWeather(county, None, purpose="daily", town=town, mode="today"), None,
                            raise_errors=True)

    # ---------- 按鈕 ----------
    async def on_press(self, interaction: discord.Interaction, custom_id: str):
        res = self.core.press(custom_id, to_user(interaction.user), self.now(), interaction.channel_id)
        if res.edit_menu:
            return await self.open_edit_menu(interaction)
        if res.content is not None:                  # 整則換掉（吃什麼的下一題）
            view = make_view(res.buttons)
            return await interaction.response.edit_message(content=self.emo(res.content, interaction.guild),
                                                           view=view)
        if res.ephemeral:
            view = make_view(res.ephemeral_buttons)
            return await interaction.response.send_message(self.emo(res.ephemeral, interaction.guild), ephemeral=True,
                                                           **({"view": view} if view else {}))
        content = interaction.message.content
        if res.strike:
            content = f"~~{content}~~"
        if res.append:
            content = f"{content}\n{self.emo(res.append, interaction.guild)}"
        await interaction.response.edit_message(content=content, view=None)
        purges = [a for a in res.actions if isinstance(a, Purge)]
        if purges:
            asyncio.create_task(self._purge(purges[0], interaction))
        if res.actions:
            await self.run_actions([a for a in res.actions if not isinstance(a, Purge)])

    # ---------- 編輯提醒：下拉選單 → 改時間／改內容／取消 ----------
    async def open_edit_menu(self, interaction: discord.Interaction):
        rems = self.core.editable(interaction.user.id)
        if not rems:
            return await interaction.response.send_message("目前沒有提醒。", ephemeral=True)
        await interaction.response.send_message("選一個來改：", view=EditMenu(self, rems), ephemeral=True)

    # ---------- 排程 ----------
    @tasks.loop(seconds=15)
    async def tick(self):
        try:
            await self.run_actions(self.core.tick(self.now(), default_channel_id=self.config.reminder_channel_id))
        except Exception:
            log.exception("排程執行失敗")

    @tick.before_loop
    async def _before_tick(self):
        await self.wait_until_ready()
