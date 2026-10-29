"""把判斷規則整理成看得懂的樣子（設定網頁「判斷邏輯」頁、測試台「邏輯」分頁用）。

關鍵字直接從 router.py／timeparse.py 的規則產生，所以這裡看到的就是 bot 實際在用的。
"""
from __future__ import annotations

import re

from . import router as R
from . import timeparse as T
from .settings import note_categories, topics, wish_lists


def words(pattern: str) -> list[str]:
    """把正規表示式粗略轉成關鍵字清單（給人看的）。"""
    p = re.sub(r"\(\?[:!<=]+[^)]*\)|\\s\*|\\s|\{[\d,]*\}|\[[^\]]*\]|NUM", "", pattern)
    p = re.sub(r"[\^$*+?()]", "", p)
    out = []
    for w in p.split("|"):
        w = w.replace("\\", "").strip()
        if w and w not in out:
            out.append(w)
    return out


def rules(s: dict) -> list[dict]:
    """依 bot 判斷的順序列出每一步：先符合的先算。"""
    verbs = [v for _, vs in wish_lists(s) for v in vs]
    return [
        {"title": "沒 @ 助理的一般聊天", "steps": [
            ("只聽指定的人", "預設只有你；設定「願望清單」可以加人", []),
            ("只記願望", "其他句子一律不回", ["想", "好想", "想要", "之後想", "有點想", "想找時間", "+ 下面願望清單的動詞"]),
        ]},
        {"title": "@ 助理時，由上往下判斷，先符合的就是答案", "steps": [
            ("1. 說明", "整句就是這些字", words(R.HELP_RE)),
            ("2. 設定發薪日", "「發薪日是5號」", ["發薪日是／在 N 號"]),
            ("3. 用編號取消", "取消詞＋編號", words(R.CANCEL_ID_RE.split("\\s*(?:")[0])),
            ("4. 用內容取消", "取消詞出現在「提醒」之前（「提醒我取消訂閱」不算）；再看有沒有「今天／明天／所有」和內容關鍵字",
             words(R.CANCEL_VERB_RE)),
            ("5. 從清單挑", "願望清單的動詞＋什麼／不知道…什麼／推薦", ["可以畫什麼", "不知道看什麼", "買什麼好", "推薦…看"]),
            ("6. 計時", "", words(R.TIMER_RE)),
            ("7. 天氣", "句子裡沒有「提醒」才算", words(R.WEATHER_RE)),
            ("8. 先拿掉次數／期限", "記下來之後從句子拿掉", ["共 N 次", "提醒我 N 次", "N 次後取消", "持續 N 天／一週", "只問今天", "到 N 號為止"]),
            ("9. 延後", "有這些字，而且拿掉時間之後沒有別的內容（「明天再提醒我交報告」是新提醒）",
             ["延到", "延後", "順延", "改到", "晚…再提醒", "再提醒我", "再叫我", "再說", "先不要"]),
            ("10. 查清單", "不是「提醒我…」開頭", ["列出", "列表", "清單", "有哪些", "有什麼", "查看", "查詢", "看一下",
                                             "我的提醒", "我的記事", "句尾「什麼？」"]),
            ("11. 願望", "沒有「提醒」；否定（不想、沒想）和問句不算", verbs),
            ("12. 記事", "沒有「提醒」", words(R.NOTE_RE)),
            ("13. 提醒", "有提醒詞，或講了時刻／相對時間（「12點睡覺」），或內容是常見提醒", words(R.REMINDER_RE)),
            ("14. 聽不懂", "以上都不是", []),
        ]},
        {"title": "提醒的細節", "steps": [
            ("下雨天", "每天看預報，會下雨才提醒", ["下雨天", "下雨的時候", "下雨的話", "會下雨", "雨天"]),
            ("發薪日", "不知道幾號會先問", ["發薪日", "每個發薪日", "發薪那天", "領薪水那天"]),
            ("看事件 → 問時間", "bot 不知道事情什麼時候發生，所以問你", words(R.EVENTS)),
            ("太模糊 → 問時間", "", ["有空的時候", "有時間的時候", "之後", "以後", "改天", "找時間", "找個晚上…", "哪天", "回頭"]),
            ("晚點", f"= {s['btn_later_minutes']} 分鐘後", words(R.LATER_RE)),
            ("一天之內的循環", "每 N 分鐘／小時：只循環當天、不會再催", []),
        ]},
        {"title": "常見提醒（內容基本上就是這些字時，建成同一種提醒）",
         "steps": [(t.name, "／".join(t.confirm[:1]), t.keywords) for t in topics(s)]},
        {"title": "時間怎麼算", "steps": [
            ("今天、明天…", "", list(("大後天", "後天", "明天", "明早", "明晚", "今天", "今晚"))),
            ("這幾天", f"= {s['few_days']} 天後", ["這幾天", "這兩天", "最近幾天", "過幾天"]),
            ("月底", "= 當月倒數第三天", ["月底", "下個月底", "每月底"]),
            ("下週", "= 下週一；「週三」= 這週三，過了就是下週三", ["下週", "週X", "下週X", "下下週X", "週末"]),
            ("早上／中午／下午／傍晚／晚上", f"沒講幾點時：{s['time_morning']}／{s['time_noon']}／{s['time_afternoon']}"
             f"／{s['time_evening']}／{s['time_night']}", list(T.PERIODS)),
            ("只講日期", f"= {s['date_only_time']}", []),
            ("沒講上下午", "1～6 點當下午；7～11 點今天過了就當晚上；「12點睡覺」是半夜", []),
            ("重複", "", ["每天", "每晚", "平日", "每週X", "每週一三五", "每個週末", "每月N號", "每年M月D號", "每N分鐘", "每隔N小時"]),
        ]},
        {"title": "願望清單與記事分類", "steps":
            [(n, "願望清單", vs) for n, vs in wish_lists(s)] + [(n, "記事", vs) for n, vs in note_categories(s)]},
    ]


def describe(it, tz) -> dict | None:
    """一句話的判斷結果（測試台和「判斷邏輯」頁的試一句用）。"""
    if it is None:
        return None
    names = {"help": "說明", "reminder": "提醒", "timer": "計時", "note": "記事", "wish": "記願望", "suggest": "從清單挑",
             "weather": "天氣", "list": "查詢", "cancel": "取消提醒", "delete_note": "刪除記事",
             "cancel_match": "用內容取消", "snooze": "延後", "payday_set": "設定發薪日", "unknown": "聽不懂"}
    parts = []
    if it.topic:
        parts.append(f"常見提醒：{it.topic}")
    if it.ask:
        parts.append({"event": f"要問時間（看事件：{it.event}）", "vague": f"要問時間（太模糊：{it.event}）",
                      "none": "要問時間"}.get(it.ask, "要問時間"))
    if it.rain:
        parts.append("下雨天")
    if it.payday:
        parts.append("發薪日")
    if it.when:
        parts.append(T.fmt_dt(it.when, tz))
    if it.phrase:
        parts.append(f"照你說的「{it.phrase}」")
    if it.repeat and it.repeat != "payday":
        parts.append("🔁" + T.describe_repeat(it.repeat))
    if it.limit_label:
        parts.append(it.limit_label)
    if it.until and not it.limit_label:
        parts.append("只到當天")
    if it.seconds and it.kind == "timer":
        parts.append(f"{it.seconds // 60} 分鐘")
    if it.wishes:
        parts.append("、".join(f"{w.category}：{w.text}" for w in it.wishes))
    if it.category and it.kind != "wish":
        parts.append(f"清單：{it.category}")
    if it.scope and it.kind == "list":
        parts.append({"reminders": "提醒", "notes": "記事", "all": "全部"}.get(it.scope, it.scope))
    if it.kind == "cancel_match":
        parts.append(("記事" if it.scope == "notes" else "提醒") + (f"・找「{it.keyword}」" if it.keyword else "")
                     + ("・今天" if it.day == 0 else "・明天" if it.day == 1 else "") + ("・全部" if it.all else ""))
    if it.ids:
        parts.append("編號 " + "、".join(map(str, it.ids)))
    if it.day and it.kind == "payday_set":
        parts.append(f"{it.day} 號")
    if it.county:
        parts.append(it.county)
    if it.text and it.kind not in ("snooze",) and not it.topic:
        parts.append(f"內容「{it.text}」")
    if it.error:
        parts.append(f"⚠️ {it.error}")
    return {"kind": it.kind, "label": names.get(it.kind, it.kind), "detail": "｜".join(parts)}
