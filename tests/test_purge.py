"""刪訊息：判斷、確認按鈕、只有主人能用，以及轉接層實際刪哪些（用假的頻道）。"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import discord

from reminderbot.bot import ReminderBot
from reminderbot.core import Assistant, Incoming, Purge, User
from reminderbot.store import MemoryStore

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=TZ)
ME, OTHER = User(7, "我"), User(8, "小明")


def core():
    return Assistant(MemoryStore(), TZ, owner_id=7)


def test_confirm_only_owner():
    c = core()
    [r] = c.handle(Incoming(ME, "賈維斯 刪掉近七天的訊息", 1, 5, mentioned=False), NOW)
    assert "近 7 天" in r.text and [b.custom_id for b in r.buttons] == ["rem:purge:7:d7", "rem:purgeno:7"]
    [r] = c.handle(Incoming(OTHER, "賈維斯 清空這個頻道", 1, 6, mentioned=False), NOW)
    assert not r.buttons
    assert c.press("rem:purge:7:d7", OTHER, NOW, 1).ephemeral                 # 別人按沒用
    res = c.press("rem:purge:7:d7", ME, NOW, 1)
    [p] = res.actions
    assert p.channel_id == 1 and p.after == int((NOW - timedelta(days=7)).timestamp()) and p.limit is None
    assert c.press("rem:purgeno:7", ME, NOW, 1).append and not c.press("rem:purgeno:7", ME, NOW, 1).actions
    assert c.press("rem:purge:7:n50", ME, NOW, 1).actions[0].limit == 50
    assert c.press("rem:purge:7:t", ME, NOW, 1).actions[0].after == int(NOW.replace(hour=0).timestamp())
    assert c.press("rem:purge:7:all", ME, NOW, 1).actions[0].after is None


def test_no_owner_configured():
    c = Assistant(MemoryStore(), TZ)
    [r] = c.handle(Incoming(ME, "賈維斯 清空這個頻道", 1, 5, mentioned=False), NOW)
    assert "OWNER_ID" in r.text and not r.buttons


def test_slash_purge():
    c = core()
    [r] = c.slash("purge", ME, NOW, 1, "近7天")
    assert r.ephemeral and r.buttons[0].custom_id == "rem:purge:7:d7"
    [r] = c.slash("purge", ME, NOW, 1, "隨便")
    assert "看不懂" in r.text
    [r] = c.slash("purge", OTHER, NOW, 1, "全部")
    assert not r.buttons


class Msg:
    def __init__(self, mid, age, pinned=False):
        self.id, self.pinned = mid, pinned
        self.created_at = discord.utils.utcnow() - age
        self.deleted = False

    async def delete(self):
        self.deleted = True


class Chan:
    def __init__(self, msgs):
        self.msgs = msgs          # 舊 → 新
        self.bulk = []

    async def history(self, limit=None, before=None, after=None, oldest_first=False):
        for m in reversed(self.msgs):
            if m.deleted or (before and m.id >= before.id) or (after and m.created_at <= after):
                continue
            yield m

    async def delete_messages(self, ms):
        assert 1 < len(ms) <= 100
        self.bulk.append(len(ms))
        for m in ms:
            m.deleted = True

    def get_partial_message(self, mid):
        return next(m for m in self.msgs if m.id == mid)

    async def send(self, *a, **k):
        pass


async def run_purge(chan, a, confirm=None):
    sent = []

    class Progress:
        async def edit(self, content):
            sent.append(content)

    async def followup_send(text, ephemeral=True, wait=False):
        sent.append(text)
        return Progress()

    async def get_channel(_):
        return chan
    fake = SimpleNamespace(_purging=set(), _channel=get_channel)
    inter = SimpleNamespace(message=confirm, followup=SimpleNamespace(send=followup_send))
    await ReminderBot._purge(fake, a, inter)
    return sent


async def test_purge_days_keeps_pinned_and_old():
    msgs = [Msg(1, timedelta(days=30)), Msg(2, timedelta(days=3), pinned=True)] + \
        [Msg(10 + i, timedelta(days=2, minutes=-i)) for i in range(150)] + [Msg(500, timedelta(seconds=5))]
    confirm = Msg(501, timedelta(seconds=1))
    confirm.flags = SimpleNamespace(ephemeral=False)
    confirm.reference = SimpleNamespace(message_id=500)
    msgs.append(confirm)
    chan = Chan(msgs)
    after = int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp())
    sent = await run_purge(chan, Purge(1, after, None, "近 7 天"), confirm)
    assert not msgs[0].deleted and not msgs[1].deleted                      # 太舊、釘選的都留著
    assert all(m.deleted for m in msgs[2:])                                 # 包含叫它那句和確認訊息
    assert chan.bulk == [100, 50] and sent[-1] == "🧹 刪好了，近 7 天共 150 則。"


async def test_purge_count_and_very_old():
    msgs = [Msg(i, timedelta(days=60 - i)) for i in range(1, 6)] + [Msg(100 + i, timedelta(hours=5 - i)) for i in range(3)]
    chan = Chan(msgs)
    sent = await run_purge(chan, Purge(1, None, 4, "最近 4 則"))
    assert [m.deleted for m in msgs] == [False, False, False, False, True, True, True, True]
    assert sent[-1].endswith("共 4 則。")


def test_scope_phrasings():
    from reminderbot.router import purge_label, purge_scope
    now = NOW.replace(hour=21, minute=5)
    cases = {"清空七點後的訊息": "今天 19:00 之後", "清空早上七點後的訊息": "今天 07:00 之後",
             "刪掉19:30以後的訊息": "今天 19:30 之後", "清空十一點後的訊息": "今天 11:00 之後",
             "清空晚上十一點後": "昨天 23:00 之後", "清空昨天晚上八點開始的訊息": "昨天 20:00 之後",
             "清空最近一百個訊息": "最近 100 則", "刪掉100筆訊息": "最近 100 則", "清空一小時內的訊息": "近 1 小時",
             "清空半小時內的訊息": "近 30 分鐘", "清空一個月內的訊息": "近 30 天", "清空這個頻道": "所有"}
    for text, label in cases.items():
        code = purge_scope(text, now)
        assert code and purge_label(code, TZ).replace(now.strftime("%m/%d"), "今天") == label, (text, code)
    assert purge_scope("清空 12345 的訊息", now) is None                    # 有數字但看不懂：不要當成全部


def test_clock_after_deletes_from_that_time():
    c = core()
    now = NOW.replace(hour=21, minute=5)
    [r] = c.handle(Incoming(ME, "賈維斯 清空七點後的訊息", 1, 5, mentioned=False), now)
    code = r.buttons[0].custom_id.split(":")[3]
    [p] = c.press(f"rem:purge:7:{code}", ME, now, 1).actions
    assert p.after == int(now.replace(hour=19, minute=0).timestamp()) and p.limit is None and "19:00" in p.label
    [r] = c.handle(Incoming(ME, "賈維斯 清空 12345 的訊息", 1, 6, mentioned=False), now)
    assert "看不懂" in r.text and not r.buttons
