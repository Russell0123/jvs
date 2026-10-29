"""core.Assistant 的完整流程（兩種 store 各跑一次，確保測試網頁和正式上線行為一致），以及設定網頁。"""
import random
from datetime import datetime, timedelta, timezone
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
            elif isinstance(a, NeedWeather) and a.purpose != "monitor":   # 背景的天氣變化檢查另外測
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
    assert "10/29 週四 15:00" in view.ephemeral and "10/30 週五 15:00" in view.ephemeral
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


def test_weather_card_and_advice(core):
    b = weather.demo_bundle(T0, "臺北市", rain_from=15)
    nw = NeedWeather("臺北市", CH, reply_to=5, rain_q=True, mode="today")
    [send] = core.weather_result(nw, b, T0)
    card = send.embed
    assert card["title"].endswith("臺北市信義區") and card["description"] == "現在 **多雲 24°C**"
    assert [f["name"][:5] for f in card["fields"][:4]] == ["08:00", "12:00", "16:00", "20:00"]
    assert card["fields"][4]["name"].startswith("今天 ") and "°C" in card["fields"][4]["name"]
    assert any(a in card["fields"][4]["value"] for a in ["帶把傘", "曬衣服", "賭運氣"])   # 會下雨 → 叮嚀帶傘
    assert send.text.startswith("今天15點後")                                         # 「會下雨嗎」直接回答
    dry = weather.demo_bundle(T0, "臺北市", rain_from=None)
    [send] = core.weather_result(NeedWeather("臺北市", CH, rain_q=True, mode="tomorrow"), dry, T0)
    assert "不會下雨" in send.text and send.embed["fields"][4]["name"].startswith("明天")
    hot = weather.demo_bundle(T0, "臺北市", rain_from=None)
    for x in hot.slots:
        x.temp = 35.0
    assert any(a in core.day_card(hot, T0.date(), T0, core.store.settings(), random.Random(1))["fields"][4]["value"]
               for a in ["補充水分", "避暑", "有點曬"])
    wk = weather.demo_bundle(T0, "臺北市", week=True)
    [send] = core.weather_result(NeedWeather("臺北市", CH, mode="weekend"), wk, T0)
    assert send.embed["title"].endswith("週末") and len(send.embed["fields"]) == 2 and send.embed["description"]


def test_weather_routes_and_location(core):
    chat = Chat(core)
    [(_, w)] = chat.say(ME, "台中天氣")
    assert isinstance(w, NeedWeather) and w.county == "臺中市"
    assert chat.say(ME, "我討厭下雨")[0][1].__class__ is Send                     # 不是天氣
    chat.say(ME, "我在高雄市苓雅區")
    [(_, w)] = chat.say(ME, "明天會下雨嗎")
    assert (w.county, w.town, w.mode, w.rain_q) == ("高雄市", "苓雅區", "tomorrow", True)
    [(_, w)] = chat.say(ME, "下週天氣如何")
    assert w.need_week


def test_weather_daily_and_monitor():
    core = Assistant(MemoryStore(), TZ, owner_id=7, home_channel_id=CH)
    core.store.set_meta("loc:7", {"county": "臺中市", "town": "西屯區"})
    acts = core.tick(T0, CH)
    mon = [a for a in acts if isinstance(a, NeedWeather)]
    assert mon[0].purpose == "monitor" and mon[0].county == "臺中市"
    assert not [a for a in core.tick(T0 + timedelta(minutes=10), CH) if isinstance(a, NeedWeather)]   # 40 分鐘一次
    dry, wet = weather.demo_bundle(T0, "臺中市", rain_from=None), weather.demo_bundle(T0, "臺中市", rain_from=21)
    assert core.weather_result(mon[0], dry, T0) == []                       # 第一次只記下來
    assert core.weather_result(mon[0], dry, T0 + timedelta(minutes=40)) == []
    [alert] = core.weather_result(mon[0], wet, T0 + timedelta(minutes=80))
    assert alert.ping and "<@7>" in alert.text and "21點後可能下雨" in alert.embed["description"]
    assert core.weather_result(mon[0], wet, T0 + timedelta(minutes=120)) == []   # 同一段不重複通知
    night = datetime(2026, 10, 29, 23, 30, tzinfo=TZ)                       # 安靜時段
    assert core.weather_result(mon[0], weather.demo_bundle(night, "臺中市", rain_from=None), night) == []
    morning = datetime(2026, 10, 30, 7, 31, tzinfo=TZ)
    daily = [a for a in core.tick(morning, CH) if isinstance(a, NeedWeather) and a.purpose == "daily"]
    assert daily and daily[0].mode == "today"
    [card] = core.weather_result(daily[0], weather.demo_bundle(morning, "臺中市", rain_from=None), morning)
    assert card.embed["fields"][4]["name"].startswith("今天")


def test_settings_change_behavior(core):
    core.store.save_settings({"fire_phrases": "嘿，{text}！", "btn_later_label": "晚點",
                              "btn_later_minutes": 5, "feature_timer": False})
    chat = Chat(core)
    chat.say(ME, "10分鐘後提醒我關火")
    [(_, f)] = chat.tick(T0 + timedelta(minutes=10))
    assert f.text == "<@7> 嘿，關火！" and [b.label for b in f.buttons] == ["好", "晚點", "明天再說"]
    assert f.buttons[1].custom_id.endswith(":5")
    assert "關著" in chat.say(ME, "計時 5 分鐘")[0][1].text


def test_weather_parse_both_formats():
    old = {"records": {"locations": [{"location": [{"locationName": "信義區", "weatherElement": [
        {"elementName": "Wx", "time": [{"startTime": "2026-10-29 15:00:00", "endTime": "2026-10-29 18:00:00",
                                        "elementValue": [{"value": "短暫陣雨", "measures": ""}, {"value": "08"}]}]},
        {"elementName": "T", "time": [{"dataTime": "2026-10-29 15:00:00", "elementValue": [{"value": "25"}]}]},
        {"elementName": "PoP6h", "time": [{"startTime": "2026-10-29 12:00:00", "endTime": "2026-10-29 18:00:00",
                                           "elementValue": [{"value": "60"}]}]}]}]}]}}
    new = {"records": {"Locations": [{"Location": [{"LocationName": "大安區"}, {"LocationName": "信義區", "WeatherElement": [
        {"ElementName": "天氣現象", "Time": [{"StartTime": "2026-10-29T15:00:00+08:00", "EndTime": "2026-10-29T18:00:00+08:00",
                                          "ElementValue": [{"Weather": "短暫陣雨", "WeatherCode": "08"}]}]},
        {"ElementName": "溫度", "Time": [{"DataTime": "2026-10-29T15:00:00+08:00", "ElementValue": [{"Temperature": "25"}]}]},
        {"ElementName": "3小時降雨機率", "Time": [{"StartTime": "2026-10-29T15:00:00+08:00",
                                              "EndTime": "2026-10-29T18:00:00+08:00",
                                              "ElementValue": [{"ProbabilityOfPrecipitation": "60"}]}]}]}]}]}}
    for data in (old, new):
        [x] = weather.parse_forecast(data, "信義區", TZ)
        assert (x.start.hour, x.wx, x.temp, x.pop) == (15, "短暫陣雨", 25.0, 60)
    cur = {"records": {"Station": [{"StationName": "臺北", "GeoInfo": {"CountyName": "臺北市"},
                                    "ObsTime": {"DateTime": "2026-10-29T14:00:00+08:00"},
                                    "WeatherElement": {"Weather": "多雲", "AirTemperature": 24.3}}]}}
    c = weather.parse_current(cur, "臺北市", TZ)
    assert (c.station, c.wx, c.temp) == ("臺北", "多雲", 24.3)
    assert weather.icon("多雲短暫陣雨") == "🌧️" and weather.icon("晴") == "☀️" and weather.icon("午後雷陣雨") == "⛈️"


async def test_web_auth_and_settings(tmp_path):
    cfg = Config(discord_token="x", channel_ids={1}, cwa_api_key="", db_path="", tz=TZ, web_host="127.0.0.1",
                 web_port=0, admin_email="me@example.com", web_password="", owner_id=7, reminder_channel_id=1)

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
    sent = chat.run(core.rain_check(weather.demo_bundle(morning, "臺北市", rain_from=7), morning))
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


def test_any_channel_but_reminders_go_home():
    core = Assistant(MemoryStore(), TZ, owner_id=7, home_channel_id=1)
    CHAT = 2
    sent = []

    def run(actions):
        for a in actions:
            if isinstance(a, Send):
                sent.append(a)
                if a.reminder_id:
                    core.sent(a.reminder_id, 100 + len(sent), a.channel_id)
            elif isinstance(a, Edit):
                sent.append(a)
    run(core.handle(Incoming(ME, "<@1000> 10分鐘後提醒我關火", CHAT, 1), T0))
    confirm = sent[-1]
    assert confirm.channel_id == CHAT and "<#1>" in confirm.text           # 在閒聊頻道回，標註會在提醒頻道響
    run(core.tick(T0 + timedelta(minutes=10), 1))
    assert sent[-1].channel_id == 1 and "關火" in sent[-1].text           # 提醒發到提醒頻道
    fired_mid = 100 + len(sent)
    # 在閒聊頻道用說的延後：回覆在閒聊頻道，改的是提醒頻道那則
    run(core.handle(Incoming(ME, "<@1000> 後天再提醒我", CHAT, 3), T0 + timedelta(minutes=11)))
    edit = next(a for a in sent if isinstance(a, Edit))
    assert edit.channel_id == 1 and edit.message_id == fired_mid and sent[-1].channel_id == CHAT
    # 在提醒頻道設的就不用標註
    run(core.handle(Incoming(ME, "<@1000> 明天提醒我繳費", 1, 4), T0))
    assert "<#" not in sent[-1].text
    # 問時間的按鈕在閒聊頻道按，確認也回在閒聊頻道
    run(core.handle(Incoming(ME, "<@1000> 有空提醒我整理照片", CHAT, 5), T0))
    pick = next(b for b in sent[-1].buttons if b.label == "明天早上")
    res = core.press(pick.custom_id, ME, T0, channel_id=CHAT)
    assert res.actions[-1].channel_id == CHAT and core.store.get_reminder(3)["channel_id"] == 1


async def test_weather_retry():
    waits, told = [], []

    async def sleep(sec):
        waits.append(sec)

    async def on_wait(err):
        told.append(str(err))

    calls = {"n": 0}

    async def flaky():                 # 前 4 次失敗，第 5 次成功
        calls["n"] += 1
        if calls["n"] < 5:
            raise weather.WeatherError("逾時")
        return weather.demo_bundle(T0, "臺北市")
    b = await weather.fetch_with_retry(flaky, 3, 5, on_wait=on_wait, sleep=sleep)
    assert b.slots and calls["n"] == 5
    assert waits == [2, 5, 300, 2] and told == ["逾時"]     # 連試 3 次 → 等 5 分鐘 → 再試

    async def broken():
        raise weather.WeatherError("壞了")
    waits.clear()
    with pytest.raises(weather.WeatherError):
        await weather.fetch_with_retry(broken, 3, 5, sleep=sleep)
    assert waits == [2, 5, 300, 2, 5]                           # 兩輪都失敗才回報
    core = Assistant(MemoryStore(), TZ, home_channel_id=CH)
    assert core.weather_wait(NeedWeather("臺北市", CH, purpose="monitor")) == []        # 自動的不吵
    assert "5 分鐘" in core.weather_wait(NeedWeather("臺北市", CH, reply_to=3))[0].text


async def test_web_basic_auth(tmp_path):
    import base64
    cfg = Config(discord_token="x", channel_ids={1}, cwa_api_key="", db_path="", tz=TZ, web_host="127.0.0.1",
                 web_port=0, admin_email="", web_password="pw123", owner_id=7, reminder_channel_id=1, web_user="me")

    async def fetch_user(i):
        raise LookupError
    bot = SimpleNamespace(config=cfg, tz=TZ, store=SqliteStore(str(tmp_path / "a.db")),
                          get_user=lambda i: None, fetch_user=fetch_user)
    client = TestClient(TestServer(make_app(bot)))
    await client.start_server()
    try:
        auth = lambda u, p: {"Authorization": "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode()}  # noqa: E731
        assert (await client.get("/")).status == 401
        assert (await client.get("/", headers=auth("me", "wrong"))).status == 401
        assert (await client.get("/", headers=auth("admin", "pw123"))).status == 401
        assert (await client.get("/", headers=auth("me", "pw123"))).status == 200
    finally:
        await client.close()


def test_names_greetings_and_owner_only():
    core = Assistant(MemoryStore(), TZ, owner_id=7, home_channel_id=CH)
    chat = Chat(core)
    talk = lambda user, text, now=T0: chat.run(core.handle(Incoming(user, text, CH, 1, mentioned=False), now))  # noqa: E731
    greets = set(core.store.settings()["greet_phrases"].splitlines()) | {"史塔克的肛門"}
    [(_, r)] = talk(MING, "嘿賈維斯")
    assert r.text in greets                                            # 任何人打招呼都會回
    [(_, r)] = talk(MING, "Jarvis 早安")
    assert r.text in greets
    assert talk(MING, "賈維斯好帥") == []                                # 只是提到，沒打招呼
    assert talk(MING, "今天天氣不錯") == []                              # 沒叫它
    assert talk(MING, "this is fine") == []                            # 英文名字要整個字
    [(_, r)] = talk(MING, "賈維思 提醒我寫作業")
    assert r.text in core.store.settings()["unknown_phrases"].splitlines() and not core.store.user_reminders(8)
    [(_, r)] = talk(ME, "嘿賈維斯 提醒我等等去洗澡")
    rem = core.store.user_reminders(7)[0]
    assert rem["text"] == "去洗澡" and rem["due_at"] == int((T0 + timedelta(minutes=90)).timestamp())
    # 主人回答問句不用 @
    talk(ME, "賈維斯 有空提醒我整理照片")
    talk(ME, "8點", T0 + timedelta(minutes=1))
    assert [r for r in core.store.user_reminders(7) if r["text"] == "整理照片"]


def test_rare_greeting_rate():
    core = Assistant(MemoryStore(), TZ, owner_id=7)
    s = core.store.settings()
    hits = sum(core.greet(s, random.Random(i))[0] == "史塔克的肛門" for i in range(2000))
    assert 50 < hits < 160                                             # 約 5%


def test_slash_commands():
    core = Assistant(MemoryStore(), TZ, owner_id=7, home_channel_id=CH)
    [h] = core.slash("help", ME, T0, 5)
    assert h.ephemeral and "/說明" in h.text and "（" not in h.text
    [st] = core.slash("settings", ME, T0, 5, settings_url="https://x.sslip.io")
    assert st.buttons[0].url == "https://x.sslip.io" and st.ephemeral
    [w] = core.slash("weather", ME, T0, 5, arg="台中明天")
    assert isinstance(w, NeedWeather) and w.county == "臺中市" and w.mode == "tomorrow" and w.ephemeral
    [card] = core.weather_result(w, weather.demo_bundle(T0, "臺中市"), T0)
    assert card.ephemeral and card.embed
    [no] = core.slash("help", MING, T0, 5)
    assert no.ephemeral and no.text in core.store.settings()["unknown_phrases"].splitlines()


def test_reply_reschedules_instead_of_new(core):
    chat = Chat(core)
    [(cid, _)] = chat.say(ME, "晚點提醒我洗澡")
    chat.say(ME, "改成十分鐘後", reply_to=cid, now=T0 + timedelta(minutes=1))
    rems = core.store.user_reminders(7)
    assert len(rems) == 1 and rems[0]["due_at"] == int((T0 + timedelta(minutes=11)).timestamp())


def test_edit_reminder(core):
    chat = Chat(core)
    chat.say(ME, "明天下午4點提醒我交報告")
    ok, msg = core.edit_reminder(1, ME, T0, when_text="後天早上9點", new_text="交期末報告")
    rem = core.store.get_reminder(1)
    assert ok and rem["text"] == "交期末報告" and datetime.fromtimestamp(rem["due_at"], TZ).day == 31
    ok, _ = core.edit_reminder(1, ME, T0, when_text="每天晚上8點")
    assert ok and core.store.get_reminder(1)["repeat"] == "daily"
    assert core.edit_reminder(1, MING, T0, new_text="x")[0] is False            # 不是他的
    assert core.edit_reminder(1, ME, T0, when_text="哈哈")[0] is False           # 看不懂的時間
    [(_, lst)] = chat.say(ME, "我有哪些提醒")
    assert lst.buttons[0].label == "✏️ 編輯"
    assert core.press(lst.buttons[0].custom_id, ME, T0).edit_menu


async def test_web_edit_reminder(tmp_path):
    cfg = Config(discord_token="x", channel_ids={1}, cwa_api_key="", db_path="", tz=TZ, web_host="127.0.0.1",
                 web_port=0, admin_email="", web_password="", owner_id=7, reminder_channel_id=1)
    store = SqliteStore(str(tmp_path / "e.db"))
    core = Assistant(store, TZ, owner_id=7)

    async def fetch_user(i):
        raise LookupError
    bot = SimpleNamespace(config=cfg, tz=TZ, store=store, core=core, get_user=lambda i: None, fetch_user=fetch_user)
    rid = store.add_reminder(channel_id=1, creator_id=7, targets=["u:7"], text="交報告",
                             due_at=int(datetime.now(TZ).timestamp()) + 3600, now=int(datetime.now(TZ).timestamp()))
    client = TestClient(TestServer(make_app(bot)))
    await client.start_server()
    try:
        assert "name='when'" in await (await client.get("/reminders")).text()
        r = await client.post(f"/reminders/{rid}/edit", data={"when": "10分鐘後", "text": "交期末報告"},
                              allow_redirects=False)
        assert r.status == 303 and "m=ok" in r.headers["Location"] and store.get_reminder(rid)["text"] == "交期末報告"
        r = await client.post(f"/reminders/{rid}/edit", data={"when": "哈哈", "text": ""}, allow_redirects=False)
        assert "e=" in r.headers["Location"]
    finally:
        await client.close()


def test_greet_template_and_image(core):
    core.store.save_settings({"greet_phrases": "{時段}，{名字}！外面{天氣}", "greet_rare_percent": 0})
    chat = Chat(core)
    [(_, r)] = chat.say(MING, "賈維斯")
    # 還沒抓過天氣：含 {天氣} 的句子是唯一一句時照用但不會留下 {天氣}
    assert "{" not in r.text and r.image == ""
    b = weather.Bundle("臺北市", "", current=weather.Current("臺北", T0, "多雲", 24.4))
    core.weather_result(NeedWeather("臺北市", CH), b, T0)
    [(_, r)] = chat.say(MING, "賈維斯", now=T0 + timedelta(minutes=1))
    assert r.text == "午安，小明！外面多雲 24°C"
    [(_, r)] = chat.say(MING, "賈維斯", now=T0 + timedelta(hours=4))     # 天氣太舊就不講
    assert "°C" not in r.text and r.text.startswith("晚上好")
    core.set_images("greet", ["a.png", "b.png"])
    [(_, r)] = chat.say(MING, "賈維斯", now=T0 + timedelta(minutes=2))
    assert r.image in ("greet/a.png", "greet/b.png")
    core.store.save_settings({"greet_image_percent": 0})
    [(_, r)] = chat.say(MING, "賈維斯", now=T0 + timedelta(minutes=3))
    assert r.image == ""


def test_greet_skips_weather_lines_without_weather(core):
    core.store.save_settings({"greet_phrases": "外面{天氣}\n嘿", "greet_rare_percent": 0})
    assert all(core.greet(core.store.settings(), random.Random(i), T0, "小明")[0] == "嘿" for i in range(10))


def test_refuse_uses_unknown_phrases(core):
    core.store.save_settings({"unknown_phrases": "蛤"})
    core.owner_id = 7
    [(_, r)] = Chat(core).say(MING, "賈維斯 明天提醒我交報告")
    assert r.text == "蛤"


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


async def test_web_images(tmp_path):
    cfg = Config(discord_token="x", channel_ids={1}, cwa_api_key="", db_path=str(tmp_path / "i.db"), tz=TZ,
                 web_host="127.0.0.1", web_port=0, admin_email="", web_password="", owner_id=7,
                 reminder_channel_id=1)
    store = SqliteStore(cfg.db_path)
    core = Assistant(store, TZ, owner_id=7)
    bot = SimpleNamespace(config=cfg, tz=TZ, store=store, core=core, get_user=lambda i: None)
    client = TestClient(TestServer(make_app(bot)))
    await client.start_server()
    try:
        from aiohttp import FormData
        form = FormData()
        form.add_field("files", PNG, filename="a.png", content_type="image/png")
        form.add_field("files", PNG, filename="b.png", content_type="image/png")
        form.add_field("files", b"hello", filename="c.txt", content_type="text/plain")
        r = await client.post("/images/greet/upload", data=form, allow_redirects=False)
        assert r.status == 303 and "err=" in r.headers["Location"]          # c.txt 不是圖
        names = core.images("greet")
        assert len(names) == 2 and (tmp_path / "images" / "greet" / names[0]).read_bytes() == PNG
        assert names[0] in await (await client.get("/images")).text()
        assert (await client.get(f"/images/greet/{names[0]}")).status == 200
        assert (await client.get("/images/greet/..%2Fi.db")).status == 404
        r = await client.post(f"/images/greet/{names[0]}/delete", allow_redirects=False)
        assert core.images("greet") == names[1:] and not (tmp_path / "images" / "greet" / names[0]).exists()
    finally:
        await client.close()
