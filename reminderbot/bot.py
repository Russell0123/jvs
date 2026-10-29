"""Discord 轉接層：把 Discord 的訊息／按鈕轉給 core.Assistant，再把它回傳的動作做出來。"""
from __future__ import annotations

import logging
from datetime import datetime

import discord
from discord.ext import tasks

from . import weather
from .config import Config
from .core import Assistant, Button, Edit, Incoming, NeedWeather, Send, User
from .store import SqliteStore

log = logging.getLogger(__name__)

PINGS = discord.AllowedMentions(everyone=False, users=True, roles=True, replied_user=False)
NO_PINGS = discord.AllowedMentions.none()
STYLES = {"success": discord.ButtonStyle.success, "danger": discord.ButtonStyle.danger,
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
    for b in buttons:
        view.add_item(CoreButton(b.custom_id, b.label, b.emoji, b.style))
    return view


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

    def now(self) -> datetime:
        return datetime.now(self.tz)

    async def setup_hook(self):
        self.add_dynamic_items(CoreButton)
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
                        content = f"{content}\n{a.append}"
                    await msg.edit(content=content, view=None if a.clear_buttons else discord.utils.MISSING)
                elif isinstance(a, NeedWeather):
                    await self._weather(a, source)
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
        text = a.text or None
        if source is not None and a.reply_to == source.id:
            msg = await source.reply(text, mention_author=False, **kwargs)
        else:
            msg = await (await self._channel(a.channel_id)).send(text, **kwargs)
        if a.reminder_id:
            self.core.sent(a.reminder_id, msg.id, msg.channel.id)

    async def _weather(self, a: NeedWeather, source: discord.Message | None, raise_errors: bool = False):
        try:
            b = await weather.fetch_bundle(self.config.cwa_api_key, a.county, a.town, self.tz,
                                           need_current=a.need_current, need_week=a.need_week)
            if b.errors:
                log.warning("天氣部分資料沒抓到：%s", b.errors)
            actions = self.core.weather_result(a, b, self.now())
        except weather.WeatherError as e:
            if raise_errors:
                raise
            log.warning("天氣查詢失敗：%s", e)
            actions = self.core.weather_error(a, str(e))
        for act in actions:
            if isinstance(act, Send):
                await self._send(act, source)

    async def post_weather_now(self):
        """設定網頁的「貼一次天氣」。"""
        county, town = self.core.location(self.config.owner_id, self.store.settings())
        await self._weather(NeedWeather(county, None, purpose="daily", town=town, mode="today"), None,
                            raise_errors=True)

    # ---------- 按鈕 ----------
    async def on_press(self, interaction: discord.Interaction, custom_id: str):
        res = self.core.press(custom_id, to_user(interaction.user), self.now(), interaction.channel_id)
        if res.ephemeral:
            return await interaction.response.send_message(res.ephemeral, ephemeral=True)
        content = interaction.message.content
        if res.strike:
            content = f"~~{content}~~"
        if res.append:
            content = f"{content}\n{res.append}"
        await interaction.response.edit_message(content=content, view=None)
        if res.actions:
            await self.run_actions(res.actions)

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
