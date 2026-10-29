"""core.Assistant 的完整流程（兩種 store 各跑一次，確保測試網頁和正式上線行為一致），以及設定網頁。"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from reminderbot import weather
from reminderbot.config import Config
from reminderbot.core import Assistant, Edit, Incoming, NeedWeather, Send, User
from reminderbot.store import MemoryStore, SqliteStore
from reminderbot.web import make_app

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 10, 29, 14, 0, tzinfo=TZ)   # 週四下午兩點
ME, MING = User(7, "我"), User(8, "小明", {50})
CH = 1


@pytest.fixture(params=["memory", "sqlite"])
def core(request, tmp_path):
    store = MemoryStore() if request.param == "memory" else SqliteStore(str(tmp_path / "t.db"))
    return Assistant(store, TZ)


class Chat:
    """模擬頻道：送出的訊息都給編號，方便延後、再提醒時找回來。"""

    def __init__(self, core):
        self.core, self.msgs, self.next_id = core, {}, 100

    def run(self, actions):
        out = []
        for a in actions:
            if isinstance(a, Send):
                self.next_id += 1
                self.msgs[self.next_id] = a
                if a.reminder_id:
                    self.core.sent(a.reminder_id, self.next_id)
                out.append((self.next_id, a))
            elif isinstance(a, Edit):
                if a.clear_buttons:
                    self.msgs[a.message_id].buttons = []
            elif isinstance(a, NeedWeather):
                out.append((None, a))
        return out

    def say(self, user, text, now=T0, mentions=(), roles=(), reply_to=None):
        return self.run(self.core.handle(Incoming(user, text, CH, 1, list(mentions), list(roles), reply_to), now))

    def tick(self, now):
        return self.run(self.core.tick(now, CH))


def short(text):
    """文案規則：短句、不加括號說明。"""
    return len(text) <= 14 and "（" not in text and "(" not in text


def test_reminder_flow_with_nag(core):
    chat = Chat(core)
    [(_, confirm)] = chat.say(ME, "10分鐘後提醒我關火")
    assert short(confirm.text) and "關火" not in confirm.text          # 不重述內容
    assert [b.label for b in confirm.buttons] == ["查看", "取消"]
    assert chat.tick(T0 + timedelta(minutes=5)) == []

    [(mid, fired)] = chat.tick(T0 + timedelta(minutes=10))
    assert fired.ping and fired.text.startswith("<@7> ") and "關火" in fired.text and "提醒：" not in fired.text
    assert [b.label for b in fired.buttons] == ["好", "等等做", "明天再說"]

    [(_, nag)] = chat.tick(T0 + timedelta(minutes=41))
    assert "關火" in nag.text and chat.msgs[mid].buttons == []
    chat.tick(T0 + timedelta(minutes=72))
    assert chat.tick(T0 + timedelta(minutes=200)) == []          # 最多再提醒 2 次
    core.press("rem:done:1", ME, T0 + timedelta(minutes=201))
    assert core.store.get_reminder(1)["status"] == "done"


def test_no_phrase_repeats_within_three(core):
    chat = Chat(core)
    replies = [chat.say(ME, f"{h}點提醒我做第{h}件事", now=T0)[0][1].text for h in range(3, 11)]
    for i in range(len(replies) - 3):
        assert replies[i] not in replies[i + 1:i + 4], replies


def test_snooze_button_and_reply(core):
    chat = Chat(core)
    chat.say(ME, "下午3點提醒我開會")
    chat.tick(T0 + timedelta(hours=1))
    assert core.press("rem:snooze:1:10", MING, T0 + timedelta(hours=1)).ephemeral   # 別人不能按
    res = core.press("rem:snooze:1:10", ME, T0 + timedelta(hours=1))
    assert "15:10" in res.append
    [(mid, _)] = chat.tick(T0 + timedelta(minutes=70))
    [(_, r)] = chat.say(ME, "延後 30 分鐘", now=T0 + timedelta(minutes=71), reply_to=mid)
    assert short(r.text)
    due = datetime.fromtimestamp(core.store.get_reminder(1)["due_at"], TZ)
    assert (due.hour, due.minute) == (15, 41)


def test_repeat_stop_button_and_view(core):
    chat = Chat(core)
    [(_, c)] = chat.say(ME, "每天下午3點提醒我喝咖啡")
    view = core.press(c.buttons[0].custom_id, ME, T0)
    assert "10/29（四）15:00" in view.ephemeral and "10/30（五）15:00" in view.ephemeral
    for day in range(2):
        [(mid, f)] = chat.tick(T0 + timedelta(days=day, hours=1))
        assert "喝咖啡" in f.text and f.buttons[-1].label == "之後都取消"
    res = core.press(f.buttons[-1].custom_id, ME, T0 + timedelta(days=1, hours=1))
    assert core.store.get_reminder(1)["status"] == "cancelled" and res.append
    assert chat.tick(T0 + timedelta(days=2, hours=1)) == []


def test_topics(core):
    chat = Chat(core)
    [(_, c)] = chat.say(ME, "我要12點睡覺")
    assert "休息" in c.text
    rem = core.store.get_reminder(1)
    assert rem["text"] == "休息" and datetime.fromtimestamp(rem["due_at"], TZ).hour == 0
    [(_, c)] = chat.say(ME, "每兩小時提醒我喝水")
    assert "喝水" in c.text
    [(_, f)] = chat.tick(T0 + timedelta(hours=2))
    assert "💧" in f.text
    # 一天之內的循環：只到當天，也不會再催
    sent = [a for m in range(121, 60 * 12) for _, a in chat.tick(T0 + timedelta(minutes=m)) if "💧" in a.text]
    assert len(sent) == 3                                          # 18、20、22 點


def test_mention_others_and_roles(core):
    chat = Chat(core)
    [(_, c)] = chat.say(ME, "明天中午提醒 <@8> 開會", mentions=[8])
    assert "<@8>" in c.text and "開會" not in c.text and not c.ping
    chat.say(ME, "明天中午提醒 <@&50> 練團", roles=[50])
    assert len(core.store.user_reminders(8)) == 1                     # 小明查得到給他的提醒
    assert core.press("rem:cancel:2", MING, T0).ephemeral is None      # 身分組成員可以取消


def test_timer_notes_lists(core):
    chat = Chat(core)
    [(_, c)] = chat.say(ME, "計時 25 分鐘")
    assert short(c.text)
    [(_, f)] = chat.tick(T0 + timedelta(minutes=25))
    assert f.text.startswith("⏱️ <@7>")
    assert chat.tick(T0 + timedelta(hours=2)) == []                    # 計時器不會再提醒

    chat.say(ME, "我想看 葬送的芙莉蓮")
    chat.say(ME, "記住 靈感：會唱歌的貓")
    chat.say(MING, "我想看 孤獨搖滾")
    [(_, lst)] = chat.say(ME, "我想看什麼")
    assert "芙莉蓮" in lst.text and "孤獨搖滾" not in lst.text and "（" not in lst.text
    [(_, d)] = chat.say(ME, "刪除靈感 2")
    assert short(d.text) and d.buttons[0].label == "復原"


def test_weather_intent_and_daily(core):
    chat = Chat(core)
    [(_, w)] = chat.say(ME, "台中天氣")
    assert isinstance(w, NeedWeather) and w.county == "臺中市"
    morning = datetime(2026, 10, 30, 7, 31, tzinfo=TZ)
    [(_, w)] = chat.tick(morning)
    assert isinstance(w, NeedWeather) and w.channel_id == CH
    assert chat.tick(morning + timedelta(minutes=1)) == []            # 一天只貼一次


def test_weather_always_has_advice(core):
    data = json.loads(Path(__file__).with_name("fixtures_weather.json").read_text())
    periods = weather.parse(data, "臺北市", TZ)
    text = core.weather_text("臺北市", periods, T0)
    assert any(a in text for a in ["帶把傘", "曬衣服", "賭運氣"])        # 降雨 60% → 下雨建議
    hot = [weather.Period(p.start, p.end, "晴", 0, 28, 35, "") for p in periods]
    text = core.weather_text("臺北市", hot, T0)
    assert any(a in text for a in ["補充水分", "避暑", "有點曬"])
    cold = [weather.Period(p.start, p.end, "陰", 0, 12, 15, "") for p in periods]
    text = core.weather_text("臺北市", cold, T0)
    assert any(a in text for a in ["外套", "偏涼", "穿太少"])


def test_settings_change_behavior(core):
    core.store.save_settings({"fire_phrases": "嘿，{text}！", "btn_later_label": "晚點",
                              "btn_later_minutes": 5, "feature_timer": False})
    chat = Chat(core)
    chat.say(ME, "10分鐘後提醒我關火")
    [(_, f)] = chat.tick(T0 + timedelta(minutes=10))
    assert f.text == "<@7> 嘿，關火！" and [b.label for b in f.buttons] == ["好", "晚點", "明天再說"]
    assert f.buttons[1].custom_id.endswith(":5")
    assert "關著" in chat.say(ME, "計時 5 分鐘")[0][1].text


def test_weather_parse():
    data = json.loads(Path(__file__).with_name("fixtures_weather.json").read_text())
    periods = weather.parse(data, "臺北市", TZ)
    assert periods[0].pop == 60 and periods[0].max_t == 26
    report = weather.format_report("臺北市", periods, T0, "帶把傘吧☔")
    assert "今天白天" in report and report.splitlines()[-2] == "帶把傘吧☔"


async def test_web_auth_and_settings(tmp_path):
    cfg = Config(discord_token="x", channel_ids={1}, cwa_api_key="", db_path="", tz=TZ, web_host="127.0.0.1",
                 web_port=0, admin_email="me@example.com", web_password="", owner_id=7)

    async def fetch_user(i):
        raise LookupError

    async def post_weather_now():
        return None
    bot = SimpleNamespace(config=cfg, tz=TZ, store=SqliteStore(str(tmp_path / "w.db")),
                          get_user=lambda i: None, fetch_user=fetch_user, post_weather_now=post_weather_now)
    client = TestClient(TestServer(make_app(bot)))
    await client.start_server()
    try:
        assert (await client.get("/")).status == 403          # 沒有 Cloudflare Access 標頭
        h = {"Cf-Access-Authenticated-User-Email": "Me@Example.com"}
        resp = await client.get("/", headers=h)
        assert resp.status == 200 and "再催" in await resp.text()
        assert "設好提醒" in await (await client.get("/replies", headers=h)).text()

        form = {k: v for k, v in bot.store.settings().items() if not isinstance(v, bool)}
        form.update(fire_phrases="嘿，{text}！", weather_daily_time="6:45", feature_reminder="on")
        resp = await client.post("/replies", data={"fire_phrases": "嘿，{text}！"}, headers=h, allow_redirects=False)
        assert resp.status == 303
        resp = await client.post("/", data=form, headers=h, allow_redirects=False)
        assert resp.status == 303
        s = bot.store.settings()
        assert s["fire_phrases"] == "嘿，{text}！"          # 存設定頁不會蓋掉回覆頁
        assert s["weather_daily_time"] == "06:45"
        assert s["feature_reminder"] is True and s["feature_timer"] is False   # 沒勾 = 關

        resp = await client.post("/", data=form, allow_redirects=False,
                                 headers={**h, "Origin": "https://evil.example"})
        assert resp.status == 403
        assert (await client.get("/reminders", headers=h)).status == 200
        assert (await client.get("/notes", headers=h)).status == 200
        assert (await client.post("/weather/test", headers=h, allow_redirects=False)).status == 303

        await client.post("/notes/add", data={"user_id": "7", "category": "想畫", "text": "柴犬"}, headers=h,
                          allow_redirects=False)
        [n] = bot.store.user_notes(7, "想畫")
        await client.post(f"/notes/{n['id']}/edit", data={"category": "想看", "text": "孤獨搖滾"}, headers=h,
                          allow_redirects=False)
        assert bot.store.get_note(n["id"])["category"] == "想看"
        assert "孤獨搖滾" in await (await client.get("/notes?c=想看", headers=h)).text()
        logic = await (await client.get("/logic?q=每兩小時提醒我喝水", headers=h)).text()
        assert "常見提醒：喝水" in logic and "用內容取消" in logic
    finally:
        await client.close()



def test_fired_buttons_later_and_tomorrow(core):
    chat = Chat(core)
    chat.say(ME, "10分鐘後提醒我關火")
    chat.tick(T0 + timedelta(minutes=10))
    later = core.press("rem:snooze:1:90", ME, T0 + timedelta(minutes=10))
    assert "15:40" in later.append                                     # 等等做 = 1.5 小時後
    core.press("rem:snooze:1:tmr", ME, T0 + timedelta(minutes=11))
    due = datetime.fromtimestamp(core.store.get_reminder(1)["due_at"], TZ)
    assert (due.day, due.hour) == (30, 15)                             # 明天再說 = 隔天下午


def test_snooze_by_text_without_reply(core):
    chat = Chat(core)
    chat.say(ME, "10分鐘後提醒我關火")
    chat.tick(T0 + timedelta(minutes=10))
    [(_, r)] = chat.say(ME, "後天再提醒我", now=T0 + timedelta(minutes=12))
    assert short(r.text) and core.store.get_reminder(1)["status"] == "pending"
    assert datetime.fromtimestamp(core.store.get_reminder(1)["due_at"], TZ).day == 31
    [(_, r)] = chat.say(ME, "晚點再提醒我", now=T0 + timedelta(minutes=13))
    assert short(r.text)


def test_ask_then_answer_by_text_and_button(core):
    chat = Chat(core)
    [(qid, q)] = chat.say(ME, "吃完飯後提醒我吃藥")
    assert "吃完飯" in q.text and short(q.text) and q.buttons
    [(_, c)] = chat.say(ME, "晚上8點吧", now=T0 + timedelta(minutes=1))
    assert short(c.text) and core.store.get_reminder(1)["status"] == "pending"
    assert chat.msgs[qid].buttons == []                                # 問句的按鈕收掉

    [(_, q)] = chat.say(ME, "有空的時候提醒我研究NAS", now=T0 + timedelta(minutes=2))
    pick = next(b for b in q.buttons if b.label == "明天早上")
    res = core.press(pick.custom_id, ME, T0 + timedelta(minutes=3))
    assert res.actions and core.store.get_reminder(2)["status"] == "pending"

    chat.say(ME, "到家一小時後提醒我洗澡", now=T0 + timedelta(minutes=4))
    chat.say(ME, "9點", now=T0 + timedelta(minutes=5))
    due = datetime.fromtimestamp(core.store.get_reminder(3)["due_at"], TZ)
    assert (due.hour, due.minute) == (22, 0)                           # 9 點到家 + 1 小時

    chat.say(ME, "之後提醒我查資料", now=T0 + timedelta(minutes=6))
    chat.say(ME, "明天", now=T0 + timedelta(minutes=7))                   # 只講日期也算回答
    assert core.store.get_reminder(4)["status"] == "pending"


def test_cancel_by_content_with_undo(core):
    chat = Chat(core)
    chat.say(ME, "每兩小時提醒我喝水")
    chat.say(ME, "明天下午4點提醒我交報告")
    [(_, r)] = chat.say(ME, "不用再提醒我喝水了")
    assert short(r.text) and core.store.get_reminder(1)["status"] == "cancelled"
    assert core.store.get_reminder(2)["status"] == "pending"
    res = core.press(r.buttons[0].custom_id, ME, T0)                   # 復原
    assert "復原" in res.append and core.store.get_reminder(1)["status"] == "pending"
    [(_, r)] = chat.say(ME, "取消今天所有提醒")
    assert r.text in ("全部清掉了。", "好，今天的提醒都取消了。")
    assert core.store.get_reminder(1)["status"] == "cancelled" and core.store.get_reminder(2)["status"] == "pending"


def test_limits(core):
    chat = Chat(core)
    chat.say(ME, "每15分鐘提醒一次，共提醒四次")
    fired = [a for m in range(1, 80) for _, a in chat.tick(T0 + timedelta(minutes=m)) if "<@7>" in a.text]
    assert len(fired) == 4 and core.store.get_reminder(1)["status"] == "done"
    assert fired[-1].buttons[-1].label != "之後都取消"                  # 最後一次就沒有「之後」了


def test_rain_and_payday(core):
    chat = Chat(core)
    [(_, c)] = chat.say(ME, "下雨天提醒我把雨衣拿出來")
    assert short(c.text) and "雨衣" not in c.text
    morning = datetime(2026, 10, 30, 7, 31, tzinfo=TZ)
    [(_, w)] = chat.tick(morning)
    assert isinstance(w, NeedWeather) and w.rain_check
    from sim.simbridge import fake_periods
    sent = chat.run(core.rain_check(fake_periods(morning), morning))
    assert "雨衣" in sent[0][1].text

    [(_, q)] = chat.say(ME, "發薪日提醒我存錢")
    assert "幾號" in q.text
    out = chat.say(ME, "發薪日是5號", now=T0 + timedelta(minutes=1))
    assert short(out[-1][1].text)
    assert datetime.fromtimestamp(core.store.get_reminder(3)["due_at"], TZ).day == 5     # #2 是下雨那次


def test_passive_wishes_only_for_owner():
    core = Assistant(MemoryStore(), TZ, owner_id=7)
    chat = Chat(core)
    passive = lambda user, text, now=T0: chat.run(core.handle(  # noqa: E731
        Incoming(user, text, CH, 1, mentioned=False), now))
    assert passive(ME, "今天天氣不錯") == []                            # 一般聊天不回
    [(_, ack)] = passive(ME, "我之後想畫海邊的燈塔")
    assert len(ack.text) <= 8                                          # 簡短的「我記住了」「好～」「要畫！」
    assert passive(MING, "我想畫柴犬") == []                            # 不是指定的人
    passive(ME, "我好想畫海邊的燈塔喔", T0 + timedelta(days=1))          # 又提到：不重複存
    notes = core.store.user_notes(7, "想畫")
    assert len(notes) == 1 and notes[0]["mentions"] == 2
    core.store.save_settings({"wish_listen_ids": "8"})
    assert passive(MING, "我想畫柴犬")                                  # 改設定後小明也會被記


def test_suggest_is_fair_and_recency_weighted():
    store = MemoryStore()
    core = Assistant(store, TZ, owner_id=7)
    for i in range(12):   # 12 項，越後面越新（第 11 項是今天提到）
        store.add_note(7, "想畫", f"題目{i}", (T0 - timedelta(days=60 - i * 5)).timestamp())
    seen, counts = set(), {f"題目{i}": 0 for i in range(12)}
    for q in range(12):
        text = core.suggest(7, "想畫", T0 + timedelta(hours=q), store.settings())
        picked = [line[1:].split("（")[0] for line in text.splitlines()[1:]]
        assert len(picked) == 5
        seen |= set(picked)
        for p in picked:
            counts[p] += 1
    assert seen == set(counts)                                         # 每一項都被提到
    assert counts["題目11"] > counts["題目0"]                           # 近期的比較常出現


def test_reply_to_bot_message_without_mention(core):
    chat = Chat(core)
    [(cid, _)] = chat.say(ME, "明天下午4點提醒我交報告")
    [(fid, _)] = chat.say(ME, "10分鐘後提醒我關火") and chat.tick(T0 + timedelta(minutes=10))
    # 回覆「設好提醒」那則說「取消」→ 取消那一個
    out = chat.say(ME, "取消", reply_to=cid, now=T0 + timedelta(minutes=11))
    assert core.store.get_reminder(1)["status"] == "cancelled" and core.store.get_reminder(2)["status"] == "fired"
    assert out[0][1].buttons[0].label == "復原"
    # 回覆響起來的那則說「後天」→ 延後那一個
    chat.say(ME, "後天", reply_to=fid, now=T0 + timedelta(minutes=12))
    rem = core.store.get_reminder(2)
    assert rem["status"] == "pending" and datetime.fromtimestamp(rem["due_at"], TZ).day == 31
    [(fid, _)] = chat.tick(T0 + timedelta(days=2, hours=-5))
    chat.say(ME, "好", reply_to=fid, now=T0 + timedelta(days=2, hours=-5))
    assert core.store.get_reminder(2)["status"] == "done"
    # 別人回覆不能動
    chat.say(ME, "每天下午3點提醒我喝咖啡")
    [(fid, _)] = chat.tick(T0 + timedelta(days=3, hours=1))
    chat.say(MING, "不用了", reply_to=fid, now=T0 + timedelta(days=3, hours=1))
    assert core.store.get_reminder(3)["status"] == "pending"
    # 回覆重複提醒的某一次說「不用了」→ 整串停掉
    chat.say(ME, "不用了", reply_to=fid, now=T0 + timedelta(days=3, hours=1))
    assert core.store.get_reminder(3)["status"] == "cancelled"
    # 回覆說的不是取消／完成／時間 → 照一般判斷
    [(_, r)] = chat.say(ME, "我想畫柴犬", reply_to=fid, now=T0 + timedelta(days=3, hours=2))
    assert core.store.user_notes(7, "想畫")
