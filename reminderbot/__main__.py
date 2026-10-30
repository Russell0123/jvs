"""python -m reminderbot：賈維斯和 .env 有填 token 的其他 bot 一起跑，共用一個設定網頁。"""
import asyncio
import logging
import os

import discord

from .bot import ReminderBot
from .config import load, load_bots
from .web import start_web

log = logging.getLogger("reminderbot")


async def run_bot(bot: ReminderBot):
    """一個 bot 登入失敗不影響其他的。"""
    try:
        await bot.start(bot.config.discord_token)
    except discord.LoginFailure:
        log.error("%s 的 token 不對，這個 bot 先不跑（.env 的 %s）", bot.config.name,
                  "DISCORD_TOKEN" if bot.config.key == "jvs" else f"{bot.config.key.upper()}_DISCORD_TOKEN")
    except discord.PrivilegedIntentsRequired:
        log.error("%s 要在 Discord Developer Portal 打開 Message Content Intent", bot.config.name)
    except Exception:
        log.exception("%s 停了", bot.config.name)
    finally:
        if not bot.is_closed():
            await bot.close()


async def amain():
    cfg = load()
    os.makedirs(os.path.dirname(cfg.db_path) or ".", exist_ok=True)
    bots = [ReminderBot(c) for c in load_bots(cfg)]
    runner = await start_web(bots)
    try:
        await asyncio.gather(*(run_bot(b) for b in bots))
    finally:
        await runner.cleanup()
        for b in bots:
            b.store.close()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(amain())


if __name__ == "__main__":
    main()
