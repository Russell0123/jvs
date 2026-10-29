"""python -m reminderbot"""
import logging
import os

from .bot import ReminderBot
from .config import load


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load()
    os.makedirs(os.path.dirname(cfg.db_path) or ".", exist_ok=True)
    ReminderBot(cfg).run(cfg.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
