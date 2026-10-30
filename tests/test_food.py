"""吃什麼：解析問題樹和食物庫、計分、一題一題按按鈕。"""
from datetime import datetime, timedelta, timezone

from reminderbot import food
from reminderbot.core import Assistant, Incoming, User
from reminderbot.store import MemoryStore

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=TZ)
ME, MING = User(7, "我"), User(8, "小明")
FOODS, ERR = food.parse_library(food.DEFAULT_LIBRARY)
TREE, QERR = food.parse_questions(food.DEFAULT_QUESTIONS)


def test_defaults_parse():
    assert len(FOODS) == 112 and not ERR and not QERR
    assert {k: len(v) for k, v in TREE.branches.items()} == {"正": 4, "爽": 4, "隨": 4}
    tofu = next(f for f in FOODS if f.name == "豆花")
    assert tofu.tags["冷熱"] == {"涼", "熱"} and tofu.tags["爽法"] == set()
    assert next(f for f in FOODS if f.name == "雞蛋糕、車輪餅")
    assert TREE.branches["正"][1].multi and not TREE.branches["正"][3].multi


def test_scoring_rules():
    lib = "A　正｜1｜重｜飯｜熱｜—｜—｜—\nB　正｜2｜重肉｜飯｜熱｜—｜—｜—\nC　正｜3｜重肉｜飯｜熱｜—｜—｜—\nD　爽｜1｜重｜飯｜熱｜—｜—｜—"
    foods, _ = food.parse_library(lib)
    # 預算 2：C 太貴排除、D 不是正餐線；B 價位剛好 +1、多肉 +1
    assert food.recommend(foods, TREE, [["正"], ["2"], ["重", "肉"], [], []], 1) == ["B", "A"]
    # 不知道不計分：只剩價位 +1
    assert food.recommend(foods, TREE, [["正"], ["1"], [], [], []], 1) == ["A"]


def test_parse_errors():
    _, errs = food.parse_library("亂寫一行\n1. 好　正｜x｜—｜飯｜熱｜—｜—｜—")
    assert len(errs) == 2
    _, errs = food.parse_questions("這一餐是？｜路線｜正=正餐／爽=吃爽\n【正】\n預算？｜價位｜1=100內")
    assert any("爽" in e for e in errs)


def test_flow_buttons():
    core = Assistant(MemoryStore(), TZ, owner_id=7)
    [r] = core.handle(Incoming(MING, "賈維斯 我要吃什麼", 1, 9, mentioned=False), NOW)   # 誰都能問
    assert r.text.startswith("🍱 1/5 這一餐是？") and [b.label for b in r.buttons] == ["正餐", "吃爽的", "隨便吃"]
    sid = r.buttons[0].custom_id.split(":")[2]

    def press(arg, who=MING):
        return core.press(f"rem:food:{sid}:{arg}", who, NOW, 1)
    assert press("0", ME).ephemeral                                       # 別人不能幫答
    res = press("0")                                                      # 正餐
    assert res.content.startswith("🍱 2/5 預算？")
    res = press("1")                                                      # 100–200
    assert "3/5" in res.content and res.buttons[-1].label == "好了 →"
    res = press("0")                                                      # 複選：重口味
    assert res.buttons[0].label.startswith("✓ ") and "重口味" in res.content
    res = press("4")                                                      # 多肉
    res = press("ok")
    assert "4/5" in res.content
    res = press(str(len(TREE.branches["正"][2].options) - 1))             # 不知道 → 直接下一題
    assert "5/5" in res.content
    res = press("0")                                                      # 熱湯
    lines = res.content.splitlines()
    assert len(lines) == 6 and [b.label for b in res.buttons] == ["幫我選一個", "重來"]
    assert "牛肉麵" in res.content
    picked = press("pick")
    assert "👉" in picked.content and any(n.split(". ")[1] in picked.content.split("👉")[1] for n in lines[1:])
    again = press("again")
    assert again.content.startswith("🍱 1/5")


def test_trigger_words():
    core = Assistant(MemoryStore(), TZ, owner_id=7)
    for t in ["賈維斯 晚餐吃什麼", "賈維斯 不知道吃什麼", "賈維斯 今天要吃啥"]:
        [r] = core.handle(Incoming(ME, t, 1, 9, mentioned=False), NOW)
        assert r.text.startswith("🍱 1/5"), t
    assert core.handle(Incoming(ME, "我要吃什麼", 1, 9, mentioned=False), NOW) == []     # 沒叫它


async def test_web_food_page(tmp_path):
    from types import SimpleNamespace

    from aiohttp.test_utils import TestClient, TestServer

    from reminderbot.config import Config
    from reminderbot.store import SqliteStore
    from reminderbot.web import make_app
    cfg = Config(discord_token="x", channel_ids={1}, cwa_api_key="", db_path=str(tmp_path / "f.db"), tz=TZ,
                 web_host="127.0.0.1", web_port=0, admin_email="", web_password="", owner_id=7, reminder_channel_id=1)
    store = SqliteStore(cfg.db_path)
    bot = SimpleNamespace(config=cfg, tz=TZ, store=store, core=Assistant(store, TZ, owner_id=7))
    client = TestClient(TestServer(make_app(bot)))
    await client.start_server()
    try:
        html = await (await client.get("/food")).text()
        assert "讀到 112 項食物" in html and "name='food_library'" in html
        form = {"food_triggers": "吃啥", "food_questions": food.DEFAULT_QUESTIONS,
                "food_library": "1. 拉麵　正爽｜3｜重｜麵｜湯｜日｜湯｜—\n亂寫"}
        r = await client.post("/food", data=form, allow_redirects=False)
        assert r.status == 303 and store.settings()["food_triggers"] == "吃啥"
        html = await (await client.get("/food")).text()
        assert "讀到 1 項食物" in html and "第 2 行看不懂" in html
        await client.post("/food/reset")
        assert len(food.parse_library(store.settings()["food_library"])[0]) == 112
    finally:
        await client.close()


def _finish_quiz(core, who):
    [r] = core.handle(Incoming(who, "賈維斯 我要吃什麼", 1, 9, mentioned=False), NOW)
    sid = r.buttons[0].custom_id.split(":")[2]
    for arg in ["2", "0", "7", "3", "3"]:          # 隨便吃、100內、感覺不知道、怎麼吃不知道、冷熱不知道
        res = core.press(f"rem:food:{sid}:{arg}", who, NOW, 1)
    assert "幫我選一個" in [b.label for b in res.buttons]


def test_complaint_after_result():
    core = Assistant(MemoryStore(), TZ, owner_id=7)
    pool = ["吃屎", "媽的問題一堆欸"]
    _finish_quiz(core, MING)
    assert core.handle(Incoming(ME, "我不想吃", 1, 10, mentioned=False), NOW) == []     # 別人說的不算
    assert core.handle(Incoming(MING, "嗯", 1, 11, mentioned=False), NOW) == []
    [r] = core.handle(Incoming(MING, "這些我都不想吃", 1, 12, mentioned=False), NOW)
    assert r.text in pool and r.reply_to == 12
    assert core.handle(Incoming(MING, "難吃", 1, 13, mentioned=False), NOW) == []       # 回過一次就不再盯


def test_complaint_only_next_three():
    core = Assistant(MemoryStore(), TZ, owner_id=7)
    _finish_quiz(core, MING)
    for i in range(3):
        assert core.handle(Incoming(MING, f"好喔{i}", 1, 20 + i, mentioned=False), NOW) == []
    assert core.handle(Incoming(MING, "討厭", 1, 30, mentioned=False), NOW) == []        # 第 4 則不管
    _finish_quiz(core, MING)
    later = NOW + timedelta(minutes=31)
    assert core.handle(Incoming(MING, "難吃", 1, 31, mentioned=False), later) == []      # 超過 30 分鐘不管
