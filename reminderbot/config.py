"""從環境變數（.env）讀設定。這些是不常改、或不能放在網頁上的東西。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _ids(raw: str) -> set[int]:
    return {int(x) for x in raw.replace(" ", "").split(",") if x}


@dataclass(frozen=True)
class Config:
    discord_token: str
    channel_ids: set[int]          # 只在這些頻道回應
    cwa_api_key: str
    db_path: str
    tz: ZoneInfo
    web_host: str
    web_port: int
    admin_email: str               # 有填就要求 Cloudflare Access 驗證過的 email 相符
    web_password: str              # 有填就多一層帳密（帳號 admin）


def load() -> Config:
    token = os.environ.get("DISCORD_TOKEN", "")
    channels = _ids(os.environ.get("CHANNEL_IDS", ""))
    if not token:
        raise SystemExit("缺少 DISCORD_TOKEN，請參考 .env.example")
    if not channels:
        raise SystemExit("缺少 CHANNEL_IDS（bot 要服務的頻道 ID），請參考 .env.example")
    return Config(
        discord_token=token,
        channel_ids=channels,
        cwa_api_key=os.environ.get("CWA_API_KEY", ""),
        db_path=os.environ.get("DB_PATH", "data/bot.db"),
        tz=ZoneInfo(os.environ.get("TZ_NAME", "Asia/Taipei")),
        web_host=os.environ.get("WEB_HOST", "127.0.0.1"),
        web_port=int(os.environ.get("WEB_PORT", "8080")),
        admin_email=os.environ.get("ADMIN_EMAIL", "").strip().lower(),
        web_password=os.environ.get("WEB_PASSWORD", ""),
    )
