"""中文時間解析（不用 LLM，全部用規則）。

從一句話裡找出時間相關的片段，算出第一次提醒的時間與重複規則，
並回傳被用掉的片段位置，讓呼叫端把它們從句子裡拿掉、剩下的就是提醒內容。

支援的說法（可以混在句子任何位置）：
  相對時間：10分鐘後、半小時後、一個半小時後、2小時30分後、3天後、下週、下個月
  日期：今天、明天、後天、大後天、今晚、明早、10/30、10月30號、2026/1/5、5號、下個月5號
  星期：週三、星期五、禮拜天、這週六、下週三、下下週一、週末、下週末
  時刻：3點、三點半、3點15分、15:30、下午3點、晚上8點、中午、晚上（用預設時刻）
  重複：每天、每晚、每週三、每週一三五、平日、每個週末、每月5號、每年3月14號、每30分鐘、每隔2小時
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

NUM = r"(?:\d+|[零〇一二兩两三四五六七八九十百]+)"
WD = "一二三四五六日天"
WEEK = r"(?:週|周|星期|禮拜|礼拜|拜)"

_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "兩": 2, "两": 2, "三": 3,
              "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

# 全形數字、冒號、斜線轉半形（長度不變，位置可以直接對回原句）
_FULLWIDTH = str.maketrans("０１２３４５６７８９：／－", "0123456789:/-")

PERIODS = {
    "凌晨": "dawn", "清晨": "morning", "早上": "morning", "早晨": "morning", "上午": "morning",
    "中午": "noon", "午後": "afternoon", "下午": "afternoon", "傍晚": "evening",
    "晚上": "night", "晚間": "night", "夜晚": "night", "夜裡": "night",
    "半夜": "midnight", "深夜": "midnight",
}

DEFAULT_PERIOD_TIMES = {
    "dawn": "06:00", "morning": "09:00", "noon": "12:00", "afternoon": "15:00",
    "evening": "18:00", "night": "20:00", "midnight": "23:30",
}


def cn2int(s: str) -> int:
    s = s.strip()
    if s.isdigit():
        return int(s)
    total, cur = 0, 0
    for ch in s:
        if ch in _CN_DIGITS:
            cur = _CN_DIGITS[ch]
        elif ch == "十":
            total += (cur or 1) * 10
            cur = 0
        elif ch == "百":
            total += (cur or 1) * 100
            cur = 0
    return total + cur


def _wd_index(ch: str) -> int:
    return 6 if ch in "日天" else "一二三四五六".index(ch)


def _add_months(d: date, months: int, day: int | None = None) -> date:
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    day = day or d.day
    return date(y, m, min(day, calendar.monthrange(y, m)[1]))


def _parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


@dataclass
class ParseResult:
    when: datetime | None = None        # 第一次提醒時間（有時區）
    repeat: str | None = None           # daily / weekly:0,2 / monthly:5 / yearly:3-14 / interval:1800
    spans: list[tuple[int, int]] = field(default_factory=list)
    matched: bool = False               # 句子裡有沒有任何時間相關的字
    dated: bool = False                 # 有沒有講到哪一天（沒有的話回覆要補「明天」之類的）
    timed: bool = False                 # 有沒有講到時刻、相對時間或重複（只說「今天」「明天」不算）
    error: str | None = None

    def phrase(self, text: str) -> str:
        """使用者講時間的原話（依出現順序接起來），回覆時照著講。"""
        parts, last = [], -1
        for a, b in sorted(self.spans):
            if a < last:
                a = last
            if a < b:
                parts.append(text[a:b].strip())
            last = max(last, b)
        return "".join(p for p in parts if p)

    def strip(self, text: str) -> str:
        """把用掉的時間片段從原句拿掉。"""
        out, last = [], 0
        for a, b in sorted(self.spans):
            if a < last:
                a = last
            out.append(text[last:a])
            out.append(" ")
            last = max(last, b)
        out.append(text[last:])
        return re.sub(r"\s+", " ", "".join(out)).strip()


class _Scanner:
    """在句子上依序套用規則；用過的片段遮掉，避免後面的規則重複吃到。"""

    def __init__(self, text: str):
        self.text = text.translate(_FULLWIDTH)
        self.masked = self.text
        self.spans: list[tuple[int, int]] = []

    def find(self, pattern: str):
        for m in re.finditer(pattern, self.masked):
            yield m

    def take(self, pattern: str):
        """找第一個符合的片段，遮掉並回傳 match；沒有就回傳 None。"""
        m = re.search(pattern, self.masked)
        if m:
            a, b = m.span()
            self.masked = self.masked[:a] + "\0" * (b - a) + self.masked[b:]
            self.spans.append((a, b))
        return m


def parse(text: str, now: datetime, period_times: dict[str, str] | None = None,
          date_only_time: str = "09:00", few_days: int = 2) -> ParseResult:
    """解析句子裡的時間。now 必須帶時區（台灣時間）。"""
    pt = {**DEFAULT_PERIOD_TIMES, **(period_times or {})}
    sc = _Scanner(text)
    tz = now.tzinfo
    today = now.date()

    repeat: str | None = None
    interval: int | None = None
    period: str | None = None
    day_offset: int | None = None
    month_offset = 0
    target_date: date | None = None
    weekday: tuple[int, int] | None = None   # (週偏移, 星期幾)
    rel: timedelta | None = None
    hour: int | None = None
    minute = 0

    # ---------- 重複 ----------
    if m := sc.take(rf"每隔?\s*({NUM}|半)\s*個?\s*(半)?\s*(分鐘|分|小時|鐘頭)"):
        n = 0.5 if m.group(1) == "半" else cn2int(m.group(1))
        if m.group(2):
            n += 0.5
        interval = int(n * (60 if m.group(3).startswith("分") else 3600))
        repeat = f"interval:{max(interval, 60)}"
    elif m := sc.take(r"每天|每日|天天|每晚|每早|每個?早上|每個?晚上"):
        repeat = "daily"
        if "晚" in m.group(0):
            period = "night"
        elif "早" in m.group(0):
            period = "morning"
    elif sc.take(rf"(每個?)?(平日|工作日|上班日)|每個?{WEEK}一\s*(到|至|~|-)\s*{WEEK}?五"):
        repeat = "weekly:0,1,2,3,4"
    elif sc.take(r"每個?(週末|周末)"):
        repeat = "weekly:5,6"
    elif m := sc.take(rf"每個?{WEEK}([{WD}](?:\s*[、,，和跟及與還有]*\s*{WEEK}?\s*[{WD}])*)"):
        days = sorted({_wd_index(c) for c in m.group(1) if c in WD})
        repeat = "weekly:" + ",".join(map(str, days))
    elif sc.take(rf"每個?{WEEK}(?![{WD}])"):
        repeat = f"weekly:{today.weekday()}"
    elif m := sc.take(rf"每年\s*({NUM})\s*月\s*({NUM})\s*(號|日)?"):
        repeat = f"yearly:{cn2int(m.group(1))}-{cn2int(m.group(2))}"
    elif sc.take(r"每個?月(的)?(月底|底)"):
        repeat = "monthly:-3"
    elif m := sc.take(rf"每個?月\s*({NUM})\s*(號|日)"):
        repeat = f"monthly:{cn2int(m.group(1))}"
    elif sc.take(r"每個?月"):
        repeat = f"monthly:{today.day}"

    # ---------- 相對時間（N 分鐘後…） ----------
    unit_re = rf"({NUM}|半)\s*個?\s*(半)?\s*(秒鐘?|分鐘|分|小時|鐘頭|天|日|週|周|星期|禮拜|月)"
    if m := sc.take(rf"((?:{unit_re}\s*(?:又|零)?\s*)+)(?:之後|以後|後)"):
        secs, days, months = 0.0, 0, 0
        for u in re.finditer(unit_re, m.group(1)):
            n = 0.5 if u.group(1) == "半" else cn2int(u.group(1))
            if u.group(2):
                n += 0.5
            unit = u.group(3)
            if unit.startswith("秒"):
                secs += n
            elif unit.startswith("分"):
                secs += n * 60
            elif unit in ("小時", "鐘頭"):
                secs += n * 3600
            elif unit in ("天", "日"):
                days += int(n)
                secs += (n - int(n)) * 86400
            elif unit == "月":
                months += int(n)
            else:
                days += int(n * 7)
        if secs:
            rel = timedelta(seconds=secs + days * 86400)
        else:
            day_offset = days
            month_offset = months
    elif sc.take(rf"下個?{WEEK}(?![{WD}末])"):
        weekday = (1, 0)                       # 「下週」= 下週一
    elif sc.take(r"下個?月(?!\s*" + NUM + r"\s*(號|日))"):
        month_offset = 1

    # ---------- 日期 ----------
    if sc.take(r"這幾天|這兩天|最近幾天|過幾天|近幾天|這陣子"):
        day_offset = (day_offset or 0) + few_days
    if m := sc.take(r"(下個?月|這個?月|本月)?\s*月底"):
        months = 1 if (m.group(1) or "").startswith("下") else 0
        first = _add_months(today.replace(day=1), months, 1)
        last = calendar.monthrange(first.year, first.month)[1]
        target_date = first.replace(day=last - 2)            # 月底 = 倒數第三天
        if target_date < today:
            first = _add_months(today.replace(day=1), 1, 1)
            target_date = first.replace(day=calendar.monthrange(first.year, first.month)[1] - 2)
    words = {"大後天": 3, "後天": 2, "明天": 1, "明日": 1, "明早": 1, "明晚": 1,
             "今天": 0, "今日": 0, "今早": 0, "今晚": 0, "今夜": 0}
    if m := sc.take("|".join(words)):
        w = m.group(0)
        day_offset = (day_offset or 0) + words[w]
        if w.endswith(("晚", "夜")):
            period = period or "night"
        elif w.endswith("早"):
            period = period or "morning"

    if m := sc.take(rf"(下下|下個|下一個|下|這個|這|本|今)?\s*{WEEK}([{WD}])"):
        shift = {"下下": 2, "下個": 1, "下一個": 1, "下": 1}.get(m.group(1) or "", 0)
        weekday = (shift, _wd_index(m.group(2)))
    elif m := sc.take(r"(下下|下個|下|這個|這|本)?\s*(週末|周末)"):
        shift = {"下下": 2, "下個": 1, "下": 1}.get(m.group(1) or "", 0)
        weekday = (shift, 5)

    if target_date:
        pass
    elif m := sc.take(r"(\d{4})\s*[/\-.年]\s*(\d{1,2})\s*[/\-.月]\s*(\d{1,2})\s*(日|號)?"):
        target_date = _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    elif m := sc.take(rf"(?<![\d:])(\d{{1,2}})\s*/\s*(\d{{1,2}})(?![\d/])|({NUM})\s*月\s*({NUM})\s*(日|號)?"):
        mo, d = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        target_date = _safe_date(today.year, cn2int(mo), cn2int(d))
        if target_date and target_date < today:
            target_date = _safe_date(today.year + 1, cn2int(mo), cn2int(d))
    elif m := sc.take(rf"下個?月\s*({NUM})\s*(號|日)"):
        target_date = _add_months(today.replace(day=1), 1, cn2int(m.group(1)))
    elif m := sc.take(rf"({NUM})\s*(號|日)(?!本|文|子|記)"):
        d = cn2int(m.group(1))
        if 1 <= d <= 31:
            target_date = _add_months(today.replace(day=1), 0, d)
            if target_date < today:
                target_date = _add_months(today.replace(day=1), 1, d)

    # ---------- 時刻 ----------
    if m := sc.take("|".join(PERIODS)):
        period = PERIODS[m.group(0)]

    if m := sc.take(r"(?<!\d)(\d{1,2})\s*:\s*(\d{2})(?!\d)"):
        hour, minute = int(m.group(1)), int(m.group(2))
    elif m := sc.take(rf"({NUM})\s*(?:點鐘|點|時)(?!點)\s*(?:(半)|(一刻)|(三刻)|({NUM})\s*(?:分鐘|分)?)?"):
        hour = cn2int(m.group(1))
        if m.group(2):
            minute = 30
        elif m.group(3):
            minute = 15
        elif m.group(4):
            minute = 45
        elif m.group(5):
            minute = cn2int(m.group(5))

    before = bool(sc.take(r"^\0*(之前|以前|前)") or sc.take(r"(?<=\0)(之前|以前|前)"))

    # 有任何時間片段被用到才算有時間
    res = ParseResult(spans=sc.spans, repeat=repeat, matched=bool(sc.spans),
                      dated=bool(target_date or weekday or day_offset is not None or month_offset or rel or repeat),
                      timed=bool(hour is not None or period or rel or repeat))
    if not res.matched:
        return res
    if hour is not None and not (0 <= hour <= 24 and 0 <= minute <= 59):
        res.error = "時間看起來不太對，再確認一下？"
        return res

    # ---------- 組合 ----------
    if rel is not None:
        res.when = now + rel
        if repeat and repeat.startswith("interval:"):
            pass
        return res

    if repeat and repeat.startswith("interval:"):
        if hour is None:
            res.when = now + timedelta(seconds=interval or 60)
            return res

    # 時刻 → (時, 分)
    explicit_clock = hour is not None
    if explicit_clock and period is None and 1 <= hour <= 6:
        # 沒講上下午的「3點」「明天1點」幾乎都是下午
        hour += 12
    if explicit_clock:
        hour = _apply_period(hour, period)
    elif period:
        t = _parse_hhmm(pt[period])
        hour, minute = t.hour, t.minute
    else:
        t = _parse_hhmm(date_only_time)
        hour, minute = t.hour, t.minute

    extra_day = 0
    if hour == 24:
        hour, extra_day = 0, 1
    tod = time(hour, minute)
    if before and explicit_clock:
        # 「8點前提醒我」→ 提早半小時
        tod = (datetime.combine(today, tod) - timedelta(minutes=30)).time()

    if repeat:
        res.when = next_occurrence(repeat, now, tod, tz)
        return res

    # 日期
    base: date | None = None
    if target_date:
        base = target_date
    elif weekday:
        shift, wd = weekday
        monday = today - timedelta(days=today.weekday())
        base = monday + timedelta(days=7 * shift + wd)
        if shift == 0 and base < today:
            base += timedelta(days=7)
    elif day_offset is not None or month_offset:
        base = today + timedelta(days=day_offset or 0)
        if month_offset:
            base = _add_months(base, month_offset)

    if base is None:
        # 只說了時刻：今天還沒過就今天，過了就看能不能是下午，不然明天
        cand = datetime.combine(today, tod, tz) + timedelta(days=extra_day)
        if cand <= now and explicit_clock and period is None and hour < 12:
            pm = cand + timedelta(hours=12)
            if pm > now:
                cand = pm
        if cand <= now:
            cand += timedelta(days=1)
        res.when = cand
        return res

    cand = datetime.combine(base, tod, tz) + timedelta(days=extra_day)
    if cand <= now:
        if base == today and explicit_clock and period is None and hour < 12 \
                and cand + timedelta(hours=12) > now:
            cand += timedelta(hours=12)
        elif base == today and not explicit_clock:
            cand = now + timedelta(hours=1)
        elif weekday and weekday[0] == 0:
            cand += timedelta(days=7)
        else:
            res.error = "這個時間已經過了喔"
            return res
    res.when = cand
    return res


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _apply_period(hour: int, period: str | None) -> int:
    if period in ("afternoon", "evening", "night") and hour < 12:
        return hour + 12
    if period == "noon" and 1 <= hour <= 5:
        return hour + 12
    if period in ("midnight",) and hour == 12:
        return 24
    if period in ("midnight",) and 6 <= hour < 12:
        return hour + 12
    if period == "night" and hour == 12:
        return 24
    if period in ("dawn", "morning") and hour == 12:
        return 0 if period == "dawn" else 12
    return hour


def parse_duration(text: str) -> tuple[int | None, list[tuple[int, int]]]:
    """計時器用：「計時 1小時30分」「倒數十分鐘」→ 秒數。"""
    t = text.translate(_FULLWIDTH)
    unit_re = rf"({NUM}|半)\s*個?\s*(半)?\s*(秒鐘?|分鐘|分|小時|鐘頭)"
    m = re.search(rf"(?:{unit_re}\s*(?:又|零)?\s*)+", t)
    if not m:
        return None, []
    secs = 0.0
    for u in re.finditer(unit_re, m.group(0)):
        n = 0.5 if u.group(1) == "半" else cn2int(u.group(1))
        if u.group(2):
            n += 0.5
        unit = u.group(3)
        secs += n * (1 if unit.startswith("秒") else 60 if unit.startswith("分") else 3600)
    return (int(secs) or None), [m.span()]


# ---------- 重複規則 ----------

def next_occurrence(rule: str, after: datetime, tod: time, tz) -> datetime:
    """回傳 after 之後、符合規則的下一個時間點。"""
    kind, _, arg = rule.partition(":")
    d = after.astimezone(tz).date()
    if kind == "interval":
        return after + timedelta(seconds=int(arg))
    if kind == "daily":
        cand = datetime.combine(d, tod, tz)
        return cand if cand > after else cand + timedelta(days=1)
    if kind == "weekly":
        days = {int(x) for x in arg.split(",") if x != ""}
        for i in range(0, 8):
            day = d + timedelta(days=i)
            cand = datetime.combine(day, tod, tz)
            if day.weekday() in days and cand > after:
                return cand
    if kind == "monthly":
        n = int(arg)
        for i in range(0, 3):
            first = _add_months(d.replace(day=1), i, 1)
            last = calendar.monthrange(first.year, first.month)[1]
            day = first.replace(day=last + n + 1 if n < 0 else min(n, last))
            cand = datetime.combine(day, tod, tz)
            if cand > after:
                return cand
    if kind == "yearly":
        mo, dd = (int(x) for x in arg.split("-"))
        for y in (d.year, d.year + 1, d.year + 2):
            day = _safe_date(y, mo, dd) or _safe_date(y, mo, 28)
            cand = datetime.combine(day, tod, tz)
            if cand > after:
                return cand
    raise ValueError(f"看不懂的重複規則：{rule}")


def describe_repeat(rule: str | None) -> str:
    if not rule:
        return ""
    kind, _, arg = rule.partition(":")
    if kind == "daily":
        return "每天"
    if kind == "weekly":
        days = [int(x) for x in arg.split(",")]
        if days == [0, 1, 2, 3, 4]:
            return "平日"
        if days == [5, 6]:
            return "每個週末"
        return "每週" + "、".join("一二三四五六日"[x] for x in days)
    if kind == "monthly":
        return "每月底" if arg.startswith("-") else f"每月 {arg} 號"
    if kind == "yearly":
        mo, d = arg.split("-")
        return f"每年 {mo}/{d}"
    if kind == "interval":
        s = int(arg)
        return f"每 {s // 3600} 小時" if s % 3600 == 0 else f"每 {s // 60} 分鐘"
    return rule


WEEKDAY_NAMES = "一二三四五六日"


def fmt_dt(dt: datetime, tz) -> str:
    """10/30（四）15:00"""
    local = dt.astimezone(tz)
    return f"{local.month}/{local.day}（{WEEKDAY_NAMES[local.weekday()]}）{local:%H:%M}"
