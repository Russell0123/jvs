"""不連 Discord：用假頻道測提醒觸發、重複、延後、再提醒，以及設定網頁。"""
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from aiohttp.test_utils import TestClient, TestServer

from reminderbot import weather
from reminderbot.bot import ReminderBot
from reminderbot.config import Config
from reminderbot.web import make_app

TZ = ZoneInfo("Asia/Taipei")


class FakeChannel:
    def __init__(self):
        self.sent = []

    async def send(self, content=None, view=None, allowed_mentions=None):
        msg = SimpleNamespace(id=1000 + len(self.sent), content=content, view=view)
        self.sent.append(msg)
        return msg

    async def fetch_message(self, mid):
        m = next(m for m in self.sent if m.id == mid)

        async def edit(**kw):
            m.view = kw.get("view", m.view)
        m.edit = edit
        return m


@pytest.fixture
async def bot(tmp_path, monkeypatch):
    cfg = Config(discord_token="x", channel_ids={1}, cwa_api_key="", db_path=str(tmp_path / "t.db"),
                 tz=TZ, web_host="127.0.0.1", web_port=0, admin_email="me@example.com", web_password="")
    b = ReminderBot(cfg)
    await b.db.open()
    ch = FakeChannel()

    async def _channel(cid):
        return ch
    monkeypatch.setattr(b, "_channel", _channel)
    b.fake = ch
    yield b
    await b.db.close()


async def test_one_off_fire_and_nag(bot):
    rid = await bot.db.add_reminder(channel_id=1, creator_id=7, targets=["u:7", "u:8"], text="交報告",
                                    due_at=int(time.time()) - 1)
    await bot.fire_due()
    assert len(bot.fake.sent) == 1
    assert "<@7> <@8> 交報告" in bot.fake.sent[0].content
    assert (await bot.db.get_reminder(rid))["status"] == "fired"

    # 時間往回撥，讓它符合再提醒條件
    await bot.db.update_reminder(rid, fired_at=int(time.time()) - 31 * 60)
    await bot.nag()
    assert len(bot.fake.sent) == 2 and "第 2 次" in bot.fake.sent[1].content
    assert bot.fake.sent[0].view is None          # 舊訊息按鈕拿掉
    assert (await bot.db.get_reminder(rid))["nag_count"] == 1


async def test_repeat_spawns_occurrence(bot):
    due = datetime.now(TZ) - timedelta(seconds=5)
    rid = await bot.db.add_reminder(channel_id=1, creator_id=7, targets=["u:7"], text="吃藥",
                                    due_at=int(due.timestamp()), repeat="daily")
    await bot.fire_due()
    parent = await bot.db.get_reminder(rid)
    assert parent["status"] == "pending"
    assert parent["due_at"] == int((due + timedelta(days=1)).timestamp())
    occ = await bot.db.reminder_by_message(bot.fake.sent[0].id)
    assert occ["parent_id"] == rid and occ["status"] == "fired"


async def test_snooze(bot):
    rid = await bot.db.add_reminder(channel_id=1, creator_id=7, targets=["u:7"], text="x",
                                    due_at=int(time.time()) - 1)
    await bot.fire_due()
    rem = await bot.db.get_reminder(rid)
    new_due = await bot._snooze_update(rem, "10")
    rem = await bot.db.get_reminder(rid)
    assert rem["status"] == "pending" and abs(rem["due_at"] - (time.time() + 600)) < 5
    assert new_due > datetime.now(TZ)


async def test_user_lists(bot):
    await bot.db.add_reminder(channel_id=1, creator_id=7, targets=["u:9"], text="給別人", due_at=int(time.time()) + 99)
    await bot.db.add_note(7, "想看", "芙莉蓮")
    assert len(await bot.db.user_reminders(9)) == 1      # 被提醒的人看得到
    assert len(await bot.db.user_reminders(8)) == 0
    text = await bot.list_text(7, "all", None)
    assert "給別人" in text and "芙莉蓮" in text


def test_weather_parse():
    data = json.loads(Path(__file__).with_name("fixtures_weather.json").read_text())
    periods = weather.parse(data, "臺北市", TZ)
    assert periods[0].pop == 60 and periods[0].max_t == 26
    report = weather.format_report("臺北市", periods, 50)
    assert "帶傘" in report and "多雲短暫陣雨" in report


async def test_web_auth_and_settings(bot):
    client = TestClient(TestServer(make_app(bot)))
    await client.start_server()
    try:
        assert (await client.get("/")).status == 403          # 沒有 Cloudflare Access 標頭
        h = {"Cf-Access-Authenticated-User-Email": "Me@Example.com"}
        resp = await client.get("/", headers=h)
        assert resp.status == 200 and "訊息格式" in await resp.text()

        form = {k: v for k, v in (await bot.db.settings()).items() if not isinstance(v, bool)}
        form.update(reminder_template="🔔 {mentions} 記得 {text}", weather_daily_time="6:45", feature_reminder="on")
        resp = await client.post("/settings", data=form, headers=h, allow_redirects=False)
        assert resp.status == 303
        s = await bot.db.settings()
        assert s["reminder_template"] == "🔔 {mentions} 記得 {text}"
        assert s["weather_daily_time"] == "06:45"
        assert s["feature_reminder"] is True and s["feature_timer"] is False   # 沒勾 = 關

        resp = await client.post("/settings", data=form, allow_redirects=False,
                                 headers={**h, "Origin": "https://evil.example"})
        assert resp.status == 403
        assert (await client.get("/reminders", headers=h)).status == 200
        assert (await client.get("/notes", headers=h)).status == 200
    finally:
        await client.close()
