from datetime import datetime, timedelta, timezone

from reminderbot.explain import describe, rules
from reminderbot.router import route
from reminderbot.settings import DEFAULTS

TZ = timezone(timedelta(hours=8))


def test_rules_cover_pipeline_and_settings():
    secs = rules(DEFAULTS)
    names = [n for sec in secs for n, _, _ in sec["steps"]]
    assert "13. 提醒" in names and "14. 聽不懂" in names and "休息" in names and "想畫" in names
    custom = rules({**DEFAULTS, "topics": "運動｜跑步/健身｜好｜該動囉"})
    assert any(n == "運動" for sec in custom for n, _, _ in sec["steps"])


def test_describe():
    d = describe(route("每兩小時提醒我喝水", datetime(2026, 10, 29, 14, tzinfo=TZ), DEFAULTS), TZ)
    assert d["label"] == "提醒" and "常見提醒：喝水" in d["detail"] and "只到當天" in d["detail"]
