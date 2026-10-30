"""好幾個 bot 一起跑：設定、資料庫、圖庫各自分開；蔣寶預設不反應；設定網頁用 /b/<代號> 切換。"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer

from reminderbot import config, images
from reminderbot.config import Config
from reminderbot.core import Assistant, Incoming, User
from reminderbot.settings import seed_profile, wish_lists
from reminderbot.store import MemoryStore, SqliteStore
from reminderbot.web import make_app

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=TZ)
ME = User(7, "我")


def base_cfg(tmp_path):
    return Config(discord_token="t1", channel_ids={1}, cwa_api_key="", db_path=str(tmp_path / "bot.db"), tz=TZ,
                  web_host="127.0.0.1", web_port=0, admin_email="", web_password="", owner_id=7, reminder_channel_id=1)


def test_load_bots(tmp_path, monkeypatch):
    main = base_cfg(tmp_path)
    monkeypatch.delenv("JBAO_DISCORD_TOKEN", raising=False)
    assert [c.key for c in config.load_bots(main)] == ["jvs"]
    monkeypatch.setenv("JBAO_DISCORD_TOKEN", "t2")
    monkeypatch.setenv("JBAO_CHANNEL_IDS", "55, 66")
    jvs, jbao = config.load_bots(main)
    assert (jbao.key, jbao.name, jbao.discord_token, jbao.reminder_channel_id) == ("jbao", "蔣寶", "t2", 55)
    assert jbao.db_path == str(tmp_path / "jbao.db") and not jbao.slash and jbao.owner_id == 7
    monkeypatch.delenv("JBAO_CHANNEL_IDS")
    assert config.load_bots(main)[1].channel_ids == {1}                    # 沒填頻道就跟賈維斯一樣


def test_jbao_silent_by_default():
    store = MemoryStore()
    seed_profile(store, "jbao")
    core = Assistant(store, TZ, owner_id=7)
    for t in ["蔣寶", "蔣寶 明天提醒我交報告", "雞巴", "蔣寶 我要吃什麼"]:
        assert core.handle(Incoming(ME, t, 1, 5, mentioned=True), NOW) == [], t
    assert core.tick(NOW.replace(hour=8, minute=5), 1) == []
    store.save_settings({"respond": True})                                  # 之後在網頁打開
    assert core.handle(Incoming(ME, "蔣寶", 1, 6, mentioned=False), NOW)
    seed_profile(store, "jbao")                                             # 只在第一次種設定
    assert store.settings()["respond"] is True


def test_migrate_lists():
    store = MemoryStore()
    store.save_settings({"wish_lists": "想畫：畫\n想學：學", "note_categories": "靈感：靈感\n備忘："})
    for cat, text in [("想畫", "貓"), ("想學", "投資"), ("靈感", "點子"), ("備忘", "牛奶"), ("想看", "電影")]:
        store.add_note(7, cat, text, 0)
    core = Assistant(store, TZ, owner_id=7)
    core.migrate()
    assert sorted(n["text"] for n in store.all_notes()) == ["牛奶", "貓", "電影"]     # 想學、靈感刪掉
    assert [n for n, _ in wish_lists(store.settings())] == ["想看", "想畫", "想買"]
    store.add_note(7, "想學", "再加", 0)
    core.migrate()                                                          # 只整理一次
    assert any(n["text"] == "再加" for n in store.all_notes())


def test_image_folders(tmp_path):
    assert images.folder(str(tmp_path / "bot.db")) == tmp_path.resolve() / "images"
    assert images.folder(str(tmp_path / "jbao.db")) == tmp_path.resolve() / "images-jbao"


async def test_web_switch_bots(tmp_path):
    main = base_cfg(tmp_path)
    bots = []
    for c in [main, replace(main, key="jbao", name="蔣寶", db_path=str(tmp_path / "jbao.db"), slash=False)]:
        store = SqliteStore(c.db_path)
        seed_profile(store, c.key)
        bots.append(SimpleNamespace(config=c, tz=TZ, store=store, core=Assistant(store, TZ, owner_id=7),
                                    get_user=lambda i: None, is_ready=lambda: True))
    client = TestClient(TestServer(make_app(bots)))
    await client.start_server()
    try:
        html = await (await client.get("/")).text()
        assert "name='respond' checked" in html and "title='蔣寶'" in html and "href='/b/jbao/'" in html
        html = await (await client.get("/b/jbao/")).text()
        assert "name='respond' checked" not in html and "href='/b/jbao/rules'" in html
        assert "action='/b/jbao/'" in html and "data-base='/b/jbao'" in html
        # 存蔣寶的設定只動蔣寶
        r = await client.post("/b/jbao/", data={"respond": "on", "bot_names": "蔣寶"}, allow_redirects=False)
        assert r.status == 303 and r.headers["Location"].startswith("/b/jbao/")
        assert bots[1].store.settings()["respond"] and bots[1].store.settings()["bot_names"] == "蔣寶"
        assert bots[0].store.settings()["bot_names"] != "蔣寶"
        # 舊網址的圖片頁改到預設回覆
        r = await client.get("/b/jbao/images", allow_redirects=False)
        assert r.headers["Location"].startswith("/b/jbao/replies")
        assert (await client.get("/b/nobody/")).status == 404
        assert (await (await client.get("/healthz")).json())["bots"] == {"jvs": True, "jbao": True}
    finally:
        await client.close()
