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

    # bot 的口吻：一行一句，每次隨機挑一句（{大括號} 會被換成內容）
    "confirm_phrases": "好，{when}{verb}{who}{text}。\n"
                       "收到，{when}再跟{who}說：{text}。\n"
                       "沒問題，{when}我會{verb}{who}{text}～\n"
                       "OK，{when}{verb}{who}{text}！",
    "ask_phrases": "你是指什麼時候？\n"
                   "我確認一下，你是說什麼時候呢？\n"
                   "要我什麼時候{verb}{who}{text}？\n"
                   "大概什麼時候比較好？",
    "snooze_phrases": "好，{when}再{verb}{who}{text}。\n"
                      "收到，{when}再說～\n"
                      "OK，那{when}再{verb}{who}。",
    "note_phrases": "好，記在「{category}」了：{text}\n"
                    "收到，幫你收進「{category}」：{text}\n"
                    "記下來了～（{category}）{text}",
    "show_exact_time": True,          # 回覆下面用小字附上實際時間和編號

    # 訊息格式
    "reminder_template": "⏰ {mentions} {text}",
    "nag_template": "🔔 {mentions} 還沒處理：{text}（第 {count} 次提醒）",
    "timer_template": "⏱️ {mentions} 計時 {duration} 到了！{text}",
    "rain_template": "☔ {mentions} 今天降雨機率 {pop}%，{text}",

    # 提醒響的時候的按鈕
    "btn_done_label": "收到",
    "btn_later_label": "等等做",
    "btn_later_minutes": 90,
    "btn_tomorrow_label": "明天再說",
    "btn_tomorrow_time": "15:00",

    # 時間
    "date_only_time": "09:00",       # 只講日期沒講幾點時
    "time_morning": "09:00",
    "time_noon": "12:00",
    "time_afternoon": "15:00",
    "time_evening": "18:00",
    "time_night": "20:00",
    "few_days": 2,                   # 「這幾天」= 幾天後
    "payday": 0,                     # 發薪日（每月幾號，0 = 沒設定；成員也可以跟 bot 說「發薪日是5號」）

    # 沒按完成時再提醒
    "nag_enabled": True,
    "nag_interval_minutes": 30,
    "nag_max": 2,

    # 天氣
    "weather_daily_enabled": True,
    "weather_daily_time": "07:30",
    "weather_county": "臺北市",
    "weather_rain_threshold": 50,    # 降雨機率超過多少提醒帶傘，也是「下雨天提醒我」的門檻

    # 願望清單：聊天裡說「我想畫…」「之後想買…」「有點想追…」就自動記下來
    # 一行一個清單：「清單名稱：動詞,動詞」
    "wish_lists": "想畫：畫畫,畫\n"
                  "想看：看,追,讀,聽\n"
                  "想買：買,入手,敗\n"
                  "想學：學,練\n"
                  "想去：去,逛,玩\n"
                  "想吃：吃,喝\n"
                  "想做：做,規劃,試試,試,寫,弄,煮",
    "wish_listen_ids": "",           # 沒 @ 也會記錄誰的願望（成員 ID，逗號分隔；空白 = 只有 OWNER_ID）
    "passive_ack": True,             # 沒 @ 時記下來要不要回一句
    "wish_ack_phrases": "我記住了\n好～\n要{verb}！\n（要{verb}）\n記下來了",
    "suggest_count": 5,
    "suggest_half_life_days": 14,    # 越近期提到的越常被挑到；過了這麼多天，加成剩一半
    "suggest_phrases": "最近可以{verb}這些：\n從你提過的挑了幾個：\n要不要試試這些？",

    # 記事分類（說「記住…」「記一下…」時用）：一行一類，最後一行是沒對到時的預設分類
    "note_categories": "靈感：靈感,點子,想法,idea,突然想到\n"
                       "主題：主題,題目\n"
                       "備忘：",
}

# 設定網頁、測試網頁共用的欄位名稱
LABELS = {
    "feature_reminder": "提醒", "feature_note": "記住（靈感／想看／想買／想做／備忘）", "feature_timer": "計時器",
    "feature_weather": "天氣", "feature_mention_others": "可以提醒別人（@其他成員）",
    "confirm_phrases": "設好提醒時的回覆（一行一句，隨機挑）", "ask_phrases": "不確定時間時的問法",
    "snooze_phrases": "延後時的回覆", "note_phrases": "記事存好的回覆",
    "show_exact_time": "回覆下面附上實際時間和編號（小字）",
    "reminder_template": "提醒時間到的訊息", "nag_template": "再次提醒的訊息", "timer_template": "計時到的訊息",
    "rain_template": "下雨天提醒的訊息",
    "btn_done_label": "按鈕：完成", "btn_later_label": "按鈕：晚點", "btn_later_minutes": "「晚點」延後幾分鐘",
    "btn_tomorrow_label": "按鈕：明天", "btn_tomorrow_time": "「明天」延到明天幾點",
    "date_only_time": "只講日期時用幾點", "time_morning": "「早上」", "time_noon": "「中午」",
    "time_afternoon": "「下午」", "time_evening": "「傍晚」", "time_night": "「晚上」",
    "few_days": "「這幾天」= 幾天後", "payday": "發薪日（每月幾號，0 = 沒設定）",
    "nag_enabled": "沒按完成會再提醒", "nag_interval_minutes": "再提醒間隔（分鐘）", "nag_max": "最多再提醒幾次",
    "weather_daily_enabled": "每天自動貼天氣", "weather_daily_time": "每天幾點看預報（也用來判斷下雨天提醒）",
    "weather_county": "縣市", "weather_rain_threshold": "降雨機率超過幾 % 算下雨",
    "note_categories": "記事分類（一行一類：「分類：關鍵字,關鍵字」，最後一行是沒對到時的預設分類）",
    "wish_lists": "願望清單（一行一個：「清單名稱：動詞,動詞」）",
    "wish_listen_ids": "沒 @ 也會記錄誰的願望（成員 ID，逗號分隔；空白 = 只有你）",
    "passive_ack": "沒 @ 時記下來會回一句",
    "wish_ack_phrases": "記下願望時的回覆（一行一句）",
    "suggest_count": "問「可以畫什麼」時挑幾個",
    "suggest_half_life_days": "近期加成的半衰期（天）",
    "suggest_phrases": "挑清單時的開頭（一行一句）",
}
HINTS = {
    "confirm_phrases": "{when} 你講的時間　{verb} 提醒／叫／問　{who} 你或被提醒的人　{text} 內容",
    "ask_phrases": "{verb} {who} {text}",
    "snooze_phrases": "{when} {verb} {who} {text}",
    "note_phrases": "{category} {text}",
    "wish_ack_phrases": "{verb} 動詞（畫／看／買…）　{text} 內容",
    "suggest_phrases": "{verb}",
    "reminder_template": "{mentions} {text} {time} {creator} {repeat}",
    "nag_template": "{mentions} {text} {count}",
    "timer_template": "{mentions} {duration} {text}",
    "rain_template": "{mentions} {pop} {text}",
}
GROUPS = [
    ("功能開關", ["feature_reminder", "feature_note", "feature_timer", "feature_weather", "feature_mention_others"]),
    ("bot 的口吻", ["confirm_phrases", "ask_phrases", "snooze_phrases", "note_phrases", "show_exact_time"]),
    ("提醒訊息", ["reminder_template", "nag_template", "timer_template", "rain_template"]),
    ("提醒按鈕", ["btn_done_label", "btn_later_label", "btn_later_minutes", "btn_tomorrow_label", "btn_tomorrow_time"]),
    ("時間", ["date_only_time", "time_morning", "time_noon", "time_afternoon", "time_evening", "time_night",
            "few_days", "payday"]),
    ("再提醒", ["nag_enabled", "nag_interval_minutes", "nag_max"]),
    ("天氣", ["weather_daily_enabled", "weather_daily_time", "weather_county", "weather_rain_threshold"]),
    ("願望清單", ["wish_lists", "wish_listen_ids", "passive_ack", "wish_ack_phrases", "suggest_count",
              "suggest_half_life_days", "suggest_phrases"]),
    ("記事", ["note_categories"]),
]
MULTILINE_KEYS = ["confirm_phrases", "ask_phrases", "snooze_phrases", "note_phrases", "note_categories",
                  "wish_lists", "wish_ack_phrases", "suggest_phrases"]

BOOL_KEYS = [k for k, v in DEFAULTS.items() if isinstance(v, bool)]
INT_KEYS = [k for k, v in DEFAULTS.items() if isinstance(v, int) and not isinstance(v, bool)]
TIME_KEYS = ["date_only_time", "time_morning", "time_noon", "time_afternoon",
             "time_evening", "time_night", "weather_daily_time", "btn_tomorrow_time"]

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


def wish_lists(settings: dict) -> list[tuple[str, list[str]]]:
    return _lines(settings["wish_lists"])


def wish_verb(name: str) -> str:
    """清單「想畫」→ 動詞「畫」。"""
    return name[1:] if name.startswith("想") and len(name) > 1 else name


def note_categories(settings: dict) -> list[tuple[str, list[str]]]:
    return _lines(settings["note_categories"]) or [("備忘", [])]


def _lines(raw) -> list[tuple[str, list[str]]]:
    out = []
    for line in str(raw).splitlines():
        if not line.strip():
            continue
        parts = re.split(r"[：:]", line, maxsplit=1)
        kws = re.split(r"[,，、]", parts[1]) if len(parts) > 1 else []
        out.append((parts[0].strip(), [k.strip() for k in kws if k.strip()]))
    return out


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
        elif k == "weather_county":
            out[k] = raw if raw in COUNTIES else default
        elif k in MULTILINE_KEYS:
            text = raw.replace("\r\n", "\n").strip()
            out[k] = text or default
        else:
            out[k] = raw or default
    return out, errors


def phrases(settings: dict, key: str) -> list[str]:
    return [line.strip() for line in str(settings[key]).splitlines() if line.strip()] or \
        [line for line in str(DEFAULTS[key]).splitlines() if line]
