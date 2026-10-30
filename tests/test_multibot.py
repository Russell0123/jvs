"""好幾個 bot 一起跑：設定、資料庫、圖庫各自分開；蔣寶預設不反應；設定網頁用 /b/<代號> 切換。"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer

from reminderbot import config, images, rules
from reminderbot.config import Config
from reminderbot.core import Assistant, Handoff, Incoming, Rename, Send, User
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


def jbao_core():
    store = MemoryStore()
    seed_profile(store, "jbao")
    return Assistant(store, TZ, owner_id=7), store


def test_jbao_relay_handoff():
    core, store = jbao_core()
    pool = store.settings()["handoff_phrases"].splitlines()
    ming = User(8, "小明")
    for i, t in enumerate(["蔣寶 明天下午3點提醒我交報告", "蔣寶今天天氣如何", "蔣寶 我要吃什麼", "蔣寶 我要不要去運動"]):
        out = core.handle(Incoming(ming, t, 1, 50 + i, mentioned=False), NOW)      # 誰叫都一樣
        [r, h] = out
        assert isinstance(r, Send) and r.reply_to == 50 + i and r.text.endswith(" {jvs}"), t
        assert r.text[:-6] in pool and isinstance(h, Handoff) and h.message_id == 50 + i and "蔣寶" not in h.text
    assert not store.user_reminders(8) and not store.all_notes()                       # 自己不建提醒
    # 沒交代事情、沒叫它、只是聊天：都不理
    for t in ["蔣寶 好帥", "我要吃什麼", "雞巴", "今天好累"]:
        assert core.handle(Incoming(ming, t, 1, 60, mentioned=False), NOW) == [], t
    assert core.tick(NOW.replace(hour=8, minute=5), 1) == []                            # 不發天氣、不提醒


def test_jbao_handoff_phrase_task_variable():
    core, store = jbao_core()
    store.save_settings({"handoff_phrases": "{任務}的事我會盯緊"})
    [r, _] = core.handle(Incoming(ME, "蔣寶 明天天氣", 1, 5, mentioned=False), NOW)
    assert r.text == "天氣的事我會盯緊 {jvs}"


def test_jbao_custom_rules_still_work():
    core, store = jbao_core()
    rule, _ = rules.from_form({"name": "x", "on": "on", "call": "no", "words": "市長", "replies": "我在"}, 1)
    rules.save(store, [rule])
    [r] = core.handle(Incoming(ME, "市長在嗎", 1, 5, mentioned=False), NOW)
    assert r.text == "我在"


def test_rename():
    core, store = jbao_core()
    store.save_settings({"nick_rules": "市長/柯文哲｜蔣市長/蔣萬安\n下台｜原名"})
    out = core.handle(Incoming(ME, "蔣寶 你是市長", 1, 5, mentioned=False), NOW)
    assert [type(a) for a in out] == [Rename] and out[0].nick in ("蔣市長", "蔣萬安")
    [a] = core.handle(Incoming(ME, "蔣寶 下台", 1, 6, mentioned=False), NOW)
    assert a.nick is None
    assert core.handle(Incoming(ME, "市長好", 1, 7, mentioned=False), NOW) == []          # 沒叫它不改
    # 賈維斯也能設
    jvs = Assistant(MemoryStore(), TZ, owner_id=7)
    jvs.store.save_settings({"nick_rules": "鋼鐵人｜J.A.R.V.I.S."})
    out = jvs.handle(Incoming(ME, "賈維斯 鋼鐵人", 1, 8, mentioned=False), NOW)
    assert any(isinstance(a, Rename) and a.nick == "J.A.R.V.I.S." for a in out)


def test_seed_steps_apply_once_and_upgrade_old():
    store = MemoryStore()
    store.set_meta("_profile", "jbao")                       # 舊版只套過第一步
    store.save_settings({"respond": False})
    seed_profile(store, "jbao")
    assert store.settings()["persona"] == "relay" and store.settings()["respond"]
    store.save_settings({"respond": False})                  # 之後自己關掉，不會被蓋回來
    seed_profile(store, "jbao")
    assert store.settings()["respond"] is False


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
        assert "href='/b/jbao/rules'" in html and "name='nick_rules'" in html
        assert "/b/jbao/reminders" not in html and "/b/jbao/food" not in html and "name='greet_words'" in html
        assert "action='/b/jbao/'" in html and "data-base='/b/jbao'" in html
        assert "<a href='/' class='' title='賈維斯'>" in html                   # 在蔣寶頁面也點得回賈維斯
        assert "href='/b/jbao/' class='on' title='蔣寶'" in html
        # 存蔣寶的設定只動蔣寶
        r = await client.post("/b/jbao/", data={"respond": "on", "bot_names": "蔣寶", "nick_rules": "市長｜蔣市長"},
                              allow_redirects=False)
        assert r.status == 303 and r.headers["Location"].startswith("/b/jbao/")
        assert bots[1].store.settings()["respond"] and bots[1].store.settings()["bot_names"] == "蔣寶"
        assert bots[1].store.settings()["nick_rules"] == "市長｜蔣市長"
        assert bots[1].store.settings()["persona"] == "relay"                              # 存設定不會改掉模式
        html = await (await client.get("/b/jbao/replies")).text()
        assert "name='handoff_phrases'" in html and "name='greet_phrases'" in html and "name='confirm_phrases'" not in html
        assert bots[0].store.settings()["bot_names"] != "蔣寶"
        # 舊網址的圖片頁改到預設回覆
        r = await client.get("/b/jbao/images", allow_redirects=False)
        assert r.headers["Location"].startswith("/b/jbao/replies")
        assert (await client.get("/b/nobody/")).status == 404
        assert (await (await client.get("/healthz")).json())["bots"] == {"jvs": True, "jbao": True}
    finally:
        await client.close()


async def test_adapter_handoff_and_mention():
    from reminderbot.bot import ReminderBot
    got = []

    class Peer:
        user = SimpleNamespace(id=999)

        async def take_over(self, message, text):
            got.append((message.id, text))
    me = SimpleNamespace(peers={"jvs": Peer()})
    assert ReminderBot._peer_mention(me, "放心 {jvs}") == "放心 <@999>"
    assert ReminderBot._peer_mention(SimpleNamespace(peers={}), "放心 {jvs}") == "放心 賈維斯"
    await ReminderBot._handoff(me, Handoff(1, 42, "明天提醒我交報告"), SimpleNamespace(id=42))
    assert got == [(42, "明天提醒我交報告")]


async def test_take_over_runs_jarvis_flow_on_own_message():
    """賈維斯接手：用自己的連線重抓訊息，當作有人叫它，照平常流程回覆原本那個人。"""
    from reminderbot.bot import ReminderBot
    store = MemoryStore()
    core = Assistant(store, TZ, owner_id=7)
    own_msg = SimpleNamespace(id=42, author=SimpleNamespace(id=8, display_name="小明", name="ming", roles=[]),
                              channel=SimpleNamespace(id=1), mentions=[], role_mentions=[])
    ran = []

    async def fetch_message(mid):
        return own_msg

    async def channel(cid):
        return SimpleNamespace(fetch_message=fetch_message)

    async def run_actions(actions, source=None):
        ran.append((actions, source))
    fake = SimpleNamespace(is_ready=lambda: True, _listens_in=lambda c: True, _channel=channel, core=core,
                           now=lambda: NOW, run_actions=run_actions, config=SimpleNamespace(name="賈維斯"))
    jbao_msg = SimpleNamespace(id=42, channel=SimpleNamespace(id=1))
    await ReminderBot.take_over(fake, jbao_msg, "今天天氣如何")
    [(actions, source)] = ran
    assert source is own_msg                                        # 回覆用的是賈維斯自己抓的訊息
    assert actions and actions[0].reply_to == 42                    # 小明還沒說在哪：先問他在哪


def test_jbao_greets():
    core, store = jbao_core()
    store.save_settings({"greet_phrases": "市民朋友好", "greet_rare_percent": 0})
    ming = User(8, "小明")
    for i, (t, m) in enumerate([("蔣寶", False), ("嗨蔣寶", False), ("", True), ("蔣寶 早安", False)]):
        [r] = core.handle(Incoming(ming, t, 1, 70 + i, mentioned=m), NOW + timedelta(minutes=i))
        assert r.text == "市民朋友好" and r.reply_to == 70 + i, t
    core.set_images("greet", ["a.png"])
    [r] = core.handle(Incoming(ming, "蔣寶", 1, 80, mentioned=False), NOW)
    assert r.image == "greet/a.png"


def test_jbao_cute_triple():
    core, store = jbao_core()
    rules.seed_rules(store, "jbao")
    [rule] = rules.load(store)
    assert rule["name"] == "可愛三連拍" and rule["send"] == "all" and rule["call"] == "yes"
    out = core.handle(Incoming(ME, "蔣寶 可愛三連拍", 1, 5, mentioned=False), NOW)
    assert out == []                                                   # 還沒放圖就不送
    core.set_images(f"r{rule['id']}", ["1.png", "2.png", "3.png"])
    out = core.handle(Incoming(User(8, "小明"), "蔣寶 可愛三連拍", 1, 6, mentioned=False), NOW)
    assert [a.image for a in out] == [f"r{rule['id']}/{n}" for n in ("1.png", "2.png", "3.png")]
    assert [a.reply_to for a in out] == [6, None, None] and all(not a.text for a in out)
    assert core.handle(Incoming(ME, "可愛三連拍", 1, 7, mentioned=False), NOW) == []   # 要叫它
    rules.save(store, [])
    rules.seed_rules(store, "jbao")                                    # 刪掉就不會再加回來
    assert rules.load(store) == []


def test_send_all_with_text_lines():
    core = Assistant(MemoryStore(), TZ, owner_id=7)
    rule, _ = rules.from_form({"on": "on", "words": "數數", "send": "all", "mode": "replace",
                               "replies": "一\n\"二\n二\"\n三"}, 1)
    rules.save(core.store, [rule])
    core.set_images("r1", ["a.png"])
    out = core.handle(Incoming(ME, "賈維斯 數數", 1, 9, mentioned=False), NOW)
    assert [(a.text, a.image) for a in out] == [("一", "r1/a.png"), ("二\n二", ""), ("三", "")]


def test_jbao_says():
    core, store = jbao_core()
    core.says_fallback = ""                                            # 蔣寶沒有預設圖
    [r] = core.slash("stark", User(8, "小明"), NOW, 1, target_id=99)
    assert r.ephemeral and "上傳" in r.text
    core.set_images("stark", ["j.png"])
    pub, ack = core.slash("stark", User(8, "小明"), NOW, 1, target_id=99)
    assert pub.reply_to == 99 and pub.image == "stark/j.png" and not pub.ephemeral and ack.ephemeral


async def test_jbao_replies_page_has_says_gallery(tmp_path):
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
        html = await (await client.get("/b/jbao/replies")).text()
        assert "Jbao says" in html and "action='/b/jbao/images/stark/upload'" in html
        assert "builtin/stark.png" not in html
        assert "builtin/stark.png" in await (await client.get("/replies")).text()
    finally:
        await client.close()


def test_jbao_echo():
    core, store = jbao_core()
    assert "蔣萬安" in store.settings()["bot_names"] and "蔣寶" in store.settings()["bot_names"]
    ming = User(8, "小明")
    cases = [("蔣萬安說今天放假", "今天放假", False), ("蔣寶說：台北第一", "台北第一", False), ("jbao說 hi", "hi", False),
             ("<@555> 說 我是市長", "我是市長", True), ("蔣萬安說 <@123> 好帥", "<@123> 好帥", False)]
    for i, (t, said, m) in enumerate(cases):
        [r] = core.handle(Incoming(ming, t, 1, 90 + i, mentioned=m), NOW)
        assert r.text == said and r.reply_to is None, t
    for t in ["蔣萬安說", "我覺得蔣萬安說的對", "他說好"]:                  # 沒內容、不是開頭、不是叫它：不理
        assert core.handle(Incoming(ming, t, 1, 99, mentioned=False), NOW) == [], t
    jvs = Assistant(MemoryStore(), TZ, owner_id=7)                      # 賈維斯預設沒開
    out = jvs.handle(Incoming(ming, "賈維斯說今天放假", 1, 5, mentioned=False), NOW)
    assert not any(isinstance(a, Send) and a.text == "今天放假" for a in out)


def test_seed_append_keeps_custom_names():
    store = MemoryStore()
    store.set_meta("_profile", "jbao")
    store.set_meta("_profile_v", 2)
    store.save_settings({"bot_names": "蔣寶,小蔣"})
    seed_profile(store, "jbao")
    assert store.settings()["bot_names"] == "蔣寶,小蔣,蔣萬安" and store.settings()["echo_enabled"]


async def test_bot_message_depths(monkeypatch):
    """bot 的訊息看層數：另一個 bot 只接 1 層；自己講的照 self_loop 接；轉介那句誰都不接。"""
    from reminderbot import bot as botmod
    monkeypatch.setattr(botmod, "PEER_WAIT", 0)
    jvs_user, jbao_user, other_bot = SimpleNamespace(id=1, bot=True), SimpleNamespace(id=2, bot=True), \
        SimpleNamespace(id=3, bot=True)
    reached = []

    def make(user, peer_user, settings):
        def listens(cid):
            reached.append(botmod.CUR_DEPTH.get())
            return False                               # 到這裡就代表會照一般流程處理
        me = SimpleNamespace(user=user, peers={"x": SimpleNamespace(user=peer_user)}, _listens_in=listens,
                             store=SimpleNamespace(settings=lambda: settings))
        me._is_peer = lambda u: botmod.ReminderBot._is_peer(me, u)
        return me
    jvs = make(jvs_user, jbao_user, {"self_loop": 0, "listen_bots": True})
    jbao = make(jbao_user, jvs_user, {"self_loop": 10, "listen_bots": True})

    async def got(me, author, mid, depth=None):
        reached.clear()
        if depth is not None:
            botmod.remember_depth(mid, depth)
        await botmod.ReminderBot.on_message(me, SimpleNamespace(author=author, id=mid, channel=SimpleNamespace(id=1)))
        return list(reached)
    assert await got(jvs, SimpleNamespace(id=9, bot=False), 10) == [0]          # 人講的照常
    assert await got(jvs, jbao_user, 11, 1) == [1]                              # 蔣寶回人的：賈維斯接
    assert await got(jvs, jbao_user, 12, 2) == []                               # 蔣寶因為賈維斯才講的：不接
    assert await got(jvs, other_bot, 13, 1) == []                               # 別的 bot：不理
    assert await got(jvs, jvs_user, 14, 1) == []                                # 賈維斯不接自己
    assert await got(jbao, jbao_user, 15, 7) == [7]                             # 蔣寶接自己，10 層以內
    assert await got(jbao, jbao_user, 16, 11) == []
    assert await got(jbao, jvs_user, 17, botmod.NO_CHAIN_DEPTH) == []           # 轉介那句
    jvs.store = SimpleNamespace(settings=lambda: {"self_loop": 0, "listen_bots": False})
    assert await got(jvs, jbao_user, 18, 1) == []                               # 關掉就不聽別的 bot


async def test_send_records_depth():
    from reminderbot import bot as botmod
    n = iter(range(700, 800))

    async def send(text, **kw):
        return SimpleNamespace(id=next(n), channel=SimpleNamespace(id=1))

    async def channel(cid):
        return SimpleNamespace(send=send, guild=None)
    me = SimpleNamespace(_channel=channel, emo=lambda t, g=None: t, _peer_mention=lambda t: t,
                         core=SimpleNamespace(sent=lambda *a: None))

    async def out(text, depth):
        token = botmod.CUR_DEPTH.set(depth)
        try:
            await botmod.ReminderBot._send(me, Send(1, text), None)
        finally:
            botmod.CUR_DEPTH.reset(token)
        return next(reversed(botmod.DEPTH.items()))
    assert await out("嘿", 0) == (700, 1)                                     # 回人的
    assert await out("嘿", 3) == (701, 4)                                     # 接龍下去
    assert await out("放心 {jvs}", 0) == (702, botmod.NO_CHAIN_DEPTH)         # 轉介那句


def test_echo_mode_add():
    core, store = jbao_core()
    rule, _ = rules.from_form({"name": "x", "on": "on", "call": "no", "words": "沈伯洋", "replies": "捲髮", "mode": "add"}, 9)
    rules.save(store, [rule])
    [r] = core.handle(Incoming(ME, "蔣寶說沈伯洋", 1, 5, mentioned=False), NOW)              # 取代：只應聲
    assert r.text == "沈伯洋"
    store.save_settings({"echo_mode": "add"})
    out = core.handle(Incoming(ME, "蔣寶說沈伯洋", 1, 6, mentioned=False), NOW + timedelta(minutes=1))
    assert [a.text for a in out] == ["沈伯洋", "捲髮"]                                       # 並存：規則也跑


def test_unicode_emoji_names():
    from reminderbot.bot import ReminderBot
    me = SimpleNamespace(emojis=[])
    assert ReminderBot.emo(me, ":milk:") == "🥛"
    assert ReminderBot.emo(me, "好 :thumbsup::fire:") == "好 👍🔥"
    assert ReminderBot.emo(me, ":jibar:") == ":jibar:"                   # 伺服器沒有、也不是內建：照原字
    assert ReminderBot.emo(me, "12:30:00") == "12:30:00"
    store = MemoryStore()
    seed_profile(store, "jbao")
    assert store.settings()["self_loop"] == 10


def test_task_words():
    jvs = Assistant(MemoryStore(), TZ, owner_id=7)
    pool = jvs.store.settings()["unknown_phrases"].splitlines()
    ming = User(8, "小明")
    for i, t in enumerate(["賈維斯 幫我寫作業", "賈維斯 給我一張圖", "賈維斯 教我畫畫", "賈維斯 能不能唱歌"]):
        for who in (ME, ming):                                         # 做不到：誰叫都拒絕
            [r] = jvs.handle(Incoming(who, t, 1, 10 + i, mentioned=False), NOW + timedelta(minutes=i))
            assert r.text in pool and jvs.last_intent.kind == "cannot", t
    jvs.handle(Incoming(ME, "賈維斯 能不能明天下午3點提醒我交報告", 1, 20, mentioned=False), NOW)
    assert jvs.store.user_reminders(7)                                 # 做得到就照做
    assert jvs.handle(Incoming(ME, "幫我寫作業", 1, 21, mentioned=False), NOW) == []   # 沒叫它不管
    # 蔣寶：「幫我…」也轉給賈維斯，{任務} 變成「這件事」
    core, store = jbao_core()
    store.save_settings({"handoff_phrases": "對於{任務}，我非常重視"})
    [r, h] = core.handle(Incoming(ming, "蔣寶 幫我寫作業", 1, 30, mentioned=False), NOW)
    assert r.text == "對於這件事，我非常重視 {jvs}" and isinstance(h, Handoff) and h.text == "幫我寫作業"
