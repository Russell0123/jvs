"""可以在設定網頁調整的項目與預設值。"""
from __future__ import annotations

import re

DEFAULTS: dict = {
    # 功能開關
    "feature_reminder": True,
    "feature_note": True,
    "feature_timer": True,
    "feature_weather": True,
    "feature_mention_others": True,

    # 訊息格式（{大括號} 會被換成內容）
    "confirm_template": "✅ 已設定 #{id}：{time}{repeat}「{text}」{targets}",
    "reminder_template": "⏰ {mentions} {text}",
    "nag_template": "🔔 {mentions} 還沒處理：{text}（第 {count} 次提醒）",
    "timer_template": "⏱️ {mentions} 計時 {duration} 到了！{text}",
    "note_saved_template": "📝 記到「{category}」了（#{id}）：{text}",

    # 時間
    "date_only_time": "09:00",       # 只講日期沒講幾點時
    "time_morning": "09:00",
    "time_noon": "12:00",
    "time_afternoon": "15:00",
    "time_evening": "18:00",
    "time_night": "20:00",
    "snooze_minutes": "10,60",       # 延後按鈕（分鐘，逗號分隔）
    "snooze_tomorrow": True,         # 是否有「明天」按鈕（用 time_morning）

    # 沒按完成時再提醒
    "nag_enabled": True,
    "nag_interval_minutes": 30,
    "nag_max": 2,

    # 天氣
    "weather_daily_enabled": True,
    "weather_daily_time": "07:30",
    "weather_county": "臺北市",
    "weather_rain_threshold": 50,    # 降雨機率超過多少提醒帶傘

    # 記事分類：一行一類，「分類：關鍵字,關鍵字」
    "note_categories": "靈感：靈感,點子,想法,idea\n"
                       "想看：想看,想讀,想聽,想玩,作品,動畫,電影,漫畫,小說,影集,番\n"
                       "主題：主題,題目,研究\n"
                       "備忘：",
}

BOOL_KEYS = [k for k, v in DEFAULTS.items() if isinstance(v, bool)]
INT_KEYS = [k for k, v in DEFAULTS.items() if isinstance(v, int) and not isinstance(v, bool)]
TIME_KEYS = ["date_only_time", "time_morning", "time_noon", "time_afternoon",
             "time_evening", "time_night", "weather_daily_time"]

COUNTIES = ["臺北市", "新北市", "基隆市", "桃園市", "新竹市", "新竹縣", "苗栗縣", "臺中市",
            "彰化縣", "南投縣", "雲林縣", "嘉義市", "嘉義縣", "臺南市", "高雄市", "屏東縣",
            "宜蘭縣", "花蓮縣", "臺東縣", "澎湖縣", "金門縣", "連江縣"]


def find_county(text: str) -> str | None:
    t = text.replace("台", "臺").replace("馬祖", "連江")
    for c in COUNTIES:
        if c in t:
            return c
    for c in COUNTIES:  # 簡稱：台中、新竹（預設市）
        if c[:2] in t:
            return c
    return None


def note_categories(settings: dict) -> list[tuple[str, list[str]]]:
    out = []
    for line in str(settings["note_categories"]).splitlines():
        if not line.strip():
            continue
        parts = re.split(r"[：:]", line, maxsplit=1)
        kws = re.split(r"[,，、]", parts[1]) if len(parts) > 1 else []
        out.append((parts[0].strip(), [k.strip() for k in kws if k.strip()]))
    return out or [("備忘", [])]


def validate(form: dict) -> tuple[dict, list[str]]:
    """把網頁表單轉成設定值，回傳 (新設定, 錯誤訊息)。"""
    errors, out = [], {}
    for k, default in DEFAULTS.items():
        if k in BOOL_KEYS:
            out[k] = form.get(k) == "on"
            continue
        raw = str(form.get(k, default)).strip()
        if k in INT_KEYS:
            try:
                out[k] = int(raw)
            except ValueError:
                errors.append(f"{k} 要填數字")
                out[k] = default
        elif k in TIME_KEYS:
            if re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", raw):
                h, m = raw.split(":")
                out[k] = f"{int(h):02d}:{m}"
            else:
                errors.append(f"{k} 格式要像 07:30")
                out[k] = default
        elif k == "snooze_minutes":
            nums = [x for x in re.split(r"[,，\s]+", raw) if x]
            if nums and all(x.isdigit() and 0 < int(x) <= 10080 for x in nums):
                out[k] = ",".join(nums)
            else:
                errors.append("延後分鐘要像 10,60")
                out[k] = default
        elif k == "weather_county":
            out[k] = raw if raw in COUNTIES else default
        else:
            out[k] = raw.replace("\r\n", "\n")
    return out, errors
