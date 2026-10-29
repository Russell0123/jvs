"""測試網頁的橋接層（在 CPython 跑；瀏覽器裡用 Pyodide 跑的是同一份）。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sim"))

from simbridge import Sim  # noqa: E402

START = 1793253600000   # 2026-10-29 14:00 台灣時間


def last(state, n=1):
    return json.loads(state)["messages"][-n:]


def test_conversation_flow():
    sim = Sim(START)
    [user, bot] = last(sim.say(1, "提醒 @小明 10分鐘後 開會"), 2)
    assert user["text"].startswith("<@1000>") and "<@2>" in user["text"]
    assert user["intent"]["kind"] == "reminder" and "開會" in user["intent"]["detail"]
    assert "<@2>" in bot["text"] and "開會" in bot["text"]

    [fired] = last(sim.advance(600))
    assert fired["ping"] and "<@2> 開會" in fired["text"]
    done = next(b["custom_id"] for b in fired["buttons"] if b["custom_id"].startswith("rem:done"))
    assert "只有" not in json.dumps(last(sim.press(2, fired["id"], done)), ensure_ascii=False)
    assert json.loads(sim.state())["reminders"][0]["status"] == "done"


def test_ephemeral_and_weather_and_settings():
    sim = Sim(START)
    sim.say(1, "10分鐘後提醒我關火")
    [fired] = last(sim.jump_next())
    [eph] = last(sim.press(3, fired["id"], fired["buttons"][0]["custom_id"]))
    assert eph["ephemeral_for"] == 3 and "不是你的" in eph["text"]
    [w] = last(sim.say(1, "天氣"))
    assert "臺北市" in w["text"] and "假資料" in w["text"]
    st = json.loads(sim.save_settings(json.dumps({"reminder_template": "🔔 {mentions} {text}", "nag_max": "0"})))
    assert st["settings"]["reminder_template"] == "🔔 {mentions} {text}"
    assert json.loads(sim.clear())["messages"] == []
