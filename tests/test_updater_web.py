"""設定網頁的「更新」頁：顯示版本、寫觸發檔、重複按被擋、顯示上次結果。"""
import json
import time
from datetime import timedelta, timezone
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer

from reminderbot import updater
from reminderbot.config import Config
from reminderbot.core import Assistant
from reminderbot.store import SqliteStore
from reminderbot.web import make_app

TZ = timezone(timedelta(hours=8))


async def client_for(tmp_path, monkeypatch, latest=None):
    data = tmp_path / "data"
    data.mkdir()
    (tmp_path / "VERSION.json").write_text(json.dumps({
        "commit": "a" * 40, "short": "aaaaaaa", "message": "舊版", "time": 1793300000,
        "remote": "https://github.com/Russell0123/jvs.git", "branch": "main", "installed_at": 1793300100}))

    async def fake_latest(remote, branch="main"):
        assert updater.github_repo(remote) == "Russell0123/jvs"
        return latest
    monkeypatch.setattr(updater, "latest", fake_latest)
    cfg = Config(discord_token="x", channel_ids={1}, cwa_api_key="", db_path=str(data / "bot.db"), tz=TZ,
                 web_host="127.0.0.1", web_port=0, admin_email="", web_password="", owner_id=7, reminder_channel_id=1)
    store = SqliteStore(cfg.db_path)
    bot = SimpleNamespace(config=cfg, tz=TZ, store=store, core=Assistant(store, TZ, owner_id=7))
    client = TestClient(TestServer(make_app(bot)))
    await client.start_server()
    return client, data


async def test_shows_versions_and_newer(tmp_path, monkeypatch):
    new = {"commit": "b" * 40, "short": "bbbbbbb", "message": "新功能", "time": 1793400000}
    client, data = await client_for(tmp_path, monkeypatch, new)
    try:
        html = await (await client.get("/update")).text()
        assert "aaaaaaa" in html and "舊版" in html and "bbbbbbb" in html and "有新版本" in html
        assert "還沒從網頁更新過" in html and "更新到最新版" in html
    finally:
        await client.close()


async def test_github_unreachable(tmp_path, monkeypatch):
    client, data = await client_for(tmp_path, monkeypatch, None)
    try:
        assert "查不到" in await (await client.get("/update")).text()
    finally:
        await client.close()


async def test_button_writes_trigger_and_blocks_repeat(tmp_path, monkeypatch):
    client, data = await client_for(tmp_path, monkeypatch)
    try:
        r = await client.post("/update", allow_redirects=False)
        assert r.status == 303 and r.headers["Location"] == "/update"
        assert (data / "update-request").exists()
        html = await (await client.get("/update")).text()
        assert "更新中" in html and "disabled" in html
        r = await client.post("/update", allow_redirects=False)                  # 重複按
        assert "busy=1" in r.headers["Location"]
        assert "正在更新" in await (await client.get("/update?busy=1")).text()
    finally:
        await client.close()


async def test_status_display(tmp_path, monkeypatch):
    client, data = await client_for(tmp_path, monkeypatch)
    try:
        now = int(time.time())
        (data / "update-status.json").write_text(json.dumps({
            "state": "done", "ok": False, "message": "新版 bbbbbbb 有問題，已退回 aaaaaaa。詳情看紀錄",
            "from": "aaaaaaa", "to": "bbbbbbb", "started": now - 60, "finished": now}, ensure_ascii=False))
        (data / "update.log").write_text("第一行\nTraceback: 壞了\n")
        html = await (await client.get("/update")).text()
        assert "❌" in html and "已退回" in html and "aaaaaaa → bbbbbbb" in html and "Traceback: 壞了" in html
        assert "disabled" not in html
        # 正在跑的狀態：按鈕停用；跑太久（超過 20 分鐘）就當作停了
        (data / "update-status.json").write_text(json.dumps({"state": "running", "started": now}))
        assert updater.running(data) and not updater.request(data)
        (data / "update-status.json").write_text(json.dumps({"state": "running", "started": now - 3600}))
        assert not updater.running(data)
    finally:
        await client.close()


def test_github_repo():
    assert updater.github_repo("git@github.com:Russell0123/jvs.git") == "Russell0123/jvs"
    assert updater.github_repo("https://github.com/Russell0123/jvs") == "Russell0123/jvs"
    assert updater.github_repo("") is None
