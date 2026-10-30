"""雪豹：被 @ 才發圖；圖庫洗牌成一輪輪發、同一輪不重複、每輪順序不同；整輪發完請賈維斯通知主人。"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer

from reminderbot import config
from reminderbot.config import Config
from reminderbot.core import Assistant, Incoming, PeerSay, Send, User
from reminderbot.settings import seed_profile
from reminderbot.store import MemoryStore, SqliteStore
from reminderbot.web import make_app

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=TZ)
ME = User(7, "我")
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def snow(n=0):
    store = MemoryStore()
    seed_profile(store, "snow")
    core = Assistant(store, TZ, owner_id=7)
    core.set_images("pics", [f"{i}.png" for i in range(n)])
    return core, store


def at(core, i, text="<@555>", at_=True):
    return core.handle(Incoming(ME, text, 1, 1000 + i, mentioned=True, at=at_), NOW)


def test_only_real_at():
    core, store = snow(3)
    assert at(core, 0, "雪豹好可愛", at_=False) == []                  # 講到名字不算
    assert core.handle(Incoming(ME, "回覆它", 1, 5, reply_to_bot_message_id=9, mentioned=True), NOW) == []
    [r] = at(core, 1)
    assert isinstance(r, Send) and r.image.startswith("pics/") and r.reply_to == 1001
    assert r.text in store.settings()["kaomoji_phrases"].splitlines()
    assert core.tick(NOW, 1) == []


def test_cycles_no_repeat_and_notice():
    core, store = snow(5)
    rounds, notices = [], 0
    for c in range(4):
        got = []
        for i in range(5):
            out = at(core, c * 10 + i)
            got.append(out[0].image)
            peer = [a for a in out if isinstance(a, PeerSay)]
            notices += len(peer)
            assert all(p.text == "雪豹庫存不夠了" and p.to == "jvs" and p.ping_owner for p in peer)
            assert bool(peer) == (i == 4), (c, i)                     # 只有整輪最後一張才通知
        assert sorted(got) == [f"pics/{i}.png" for i in range(5)]       # 同一輪不重複
        rounds.append(got)
    assert notices == 4 and all(a != b for a, b in zip(rounds, rounds[1:]))   # 每輪順序不同
    assert all(a[-1] != b[0] for a, b in zip(rounds, rounds[1:]))              # 換輪時不會連發同一張
    assert store.get_meta("pic_bag")["rounds"] == 4


def test_new_images_join_current_round_and_deleted_skipped():
    core, store = snow(3)
    first = at(core, 0)[0].image
    core.set_images("pics", ["0.png", "1.png", "2.png", "new.png"])
    rest = [at(core, i)[0].image for i in range(1, 4)]
    assert sorted([first] + rest) == ["pics/0.png", "pics/1.png", "pics/2.png", "pics/new.png"]
    core.set_images("pics", ["0.png"])
    assert at(core, 9)[0].image == "pics/0.png"


def test_empty_gallery():
    core, store = snow(0)
    [r] = at(core, 0)
    assert r.image == "" and r.text


def test_load_bots_snow(tmp_path, monkeypatch):
    main = Config(discord_token="t1", channel_ids={1}, cwa_api_key="", db_path=str(tmp_path / "bot.db"), tz=TZ,
                  web_host="127.0.0.1", web_port=0, admin_email="", web_password="", owner_id=7, reminder_channel_id=1)
    monkeypatch.delenv("JBAO_DISCORD_TOKEN", raising=False)
    monkeypatch.setenv("SNOW_DISCORD_TOKEN", "t3")
    [_, s] = config.load_bots(main)
    assert (s.key, s.name, s.db_path) == ("snow", "雪豹", str(tmp_path / "snow.db"))


async def test_snow_web(tmp_path):
    main = Config(discord_token="t1", channel_ids={1}, cwa_api_key="", db_path=str(tmp_path / "bot.db"), tz=TZ,
                  web_host="127.0.0.1", web_port=0, admin_email="", web_password="", owner_id=7, reminder_channel_id=1)
    bots = []
    for c in [main, replace(main, key="snow", name="雪豹", db_path=str(tmp_path / "snow.db"), slash=False)]:
        store = SqliteStore(c.db_path)
        seed_profile(store, c.key)
        bots.append(SimpleNamespace(config=c, tz=TZ, store=store, core=Assistant(store, TZ, owner_id=7),
                                    get_user=lambda i: None, is_ready=lambda: True))
    snowbot = bots[1]
    client = TestClient(TestServer(make_app(bots)))
    await client.start_server()
    try:
        html = await (await client.get("/b/snow/replies")).text()
        assert "folderpick" in html and "webkitdirectory" in html and "name='kaomoji_phrases'" in html
        assert "/b/snow/rules" not in html and "Stark says" not in html
        for name in ["2.png", "10.png", "1.png"]:                     # 一張一張送（資料夾上傳也是這樣）
            form = FormData()
            form.add_field("files", PNG, filename=name, content_type="image/png")
            r = await client.post("/b/snow/images/pics/upload", data=form)
            assert r.status == 200
        assert len(snowbot.core.images("pics")) == 3 and (tmp_path / "images-snow" / "pics").is_dir()
        assert "共 3 張" in await (await client.get("/b/snow/replies")).text()
        snowbot.core.next_pic(__import__("random").Random(1))
        await client.post("/b/snow/images/pics/reshuffle")
        assert snowbot.store.get_meta("pic_bag")["left"] == []
        await client.post("/b/snow/images/pics/clear")
        assert snowbot.core.images("pics") == [] and not list((tmp_path / "images-snow" / "pics").iterdir())
        html = await (await client.get("/b/snow/")).text()
        assert "name='respond'" in html and "name='greet_words'" not in html
    finally:
        await client.close()


async def test_peer_say_goes_to_jarvis_home_channel():
    from reminderbot.bot import ReminderBot
    sent = []

    async def _send(a, source):
        sent.append(a)
    jvs = SimpleNamespace(config=SimpleNamespace(owner_id=7, reminder_channel_id=42), _send=_send)
    await ReminderBot.say_home(jvs, "雪豹庫存不夠了")
    [a] = sent
    assert a.channel_id == 42 and a.text == "<@7> 雪豹庫存不夠了" and a.ping and a.reply_to is None
