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
    channel_ids: set[int]          # 提醒頻道（設定關掉「所有頻道都回應」時，也只在這些頻道回應）
    reminder_channel_id: int       # 提醒一律發到這裡（CHANNEL_IDS 的第一個）
    cwa_api_key: str
    db_path: str
    tz: ZoneInfo
    web_host: str
    web_port: int
    admin_email: str               # 有填就要求 Cloudflare Access 驗證過的 email 相符
    web_password: str              # 有填就要輸入帳密才能進設定網頁
    owner_id: int | None           # 你的 Discord 使用者 ID：沒 @ 也會記錄你的「我想畫…」
    web_user: str = "admin"        # 設定網頁的帳號
    public_url: str = ""           # 設定網頁的公開網址（/設定 的按鈕會連過去）


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
        reminder_channel_id=int(os.environ["CHANNEL_IDS"].replace(" ", "").split(",")[0]),
        cwa_api_key=os.environ.get("CWA_API_KEY", ""),
        db_path=os.environ.get("DB_PATH", "data/bot.db"),
        tz=ZoneInfo(os.environ.get("TZ_NAME", "Asia/Taipei")),
        web_host=os.environ.get("WEB_HOST", "127.0.0.1"),
        web_port=int(os.environ.get("WEB_PORT", "8080")),
        admin_email=os.environ.get("ADMIN_EMAIL", "").strip().lower(),
        web_password=os.environ.get("WEB_PASSWORD", ""),
        web_user=os.environ.get("WEB_USER", "admin").strip() or "admin",
        public_url=os.environ.get("WEB_PUBLIC_URL", "").strip(),
        owner_id=int(os.environ["OWNER_ID"]) if os.environ.get("OWNER_ID", "").strip().isdigit() else None,
    )
