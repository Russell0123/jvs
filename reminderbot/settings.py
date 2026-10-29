"""可以在設定網頁調整的項目與預設值。"""
from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULTS: dict = {
    # 功能開關
    "feature_reminder": True,
    "feature_note": True,
    "feature_timer": True,
    "feature_weather": True,
    "feature_mention_others": True,
    "all_channels": True,            # 所有頻道都聽、都回；提醒一律發到提醒頻道（CHANNEL_IDS 第一個）

    # bot 的口吻：一行一句，每次隨機挑，最近用過的 3 句不會馬上再用
    # 規則：短句（3～12 字）、不重述你說的內容、不加括號說明
    "confirm_phrases": "收到🫡\n好，我記著。\n到時候叫你。\n安排上了。\n記起來了。\n交給我吧。\n放心，我記得。",
    "confirm_when_phrases": "好，{when}叫你。\n收到，{when}再跟你說。\nOK，{when}提醒你。",
    "confirm_other_phrases": "好，到時候叫{who}。\n收到，會提醒{who}。\n交給我。",
    "repeat_phrases": "好，我定期提醒你。\n沒問題，交給我。\n收到，我會固定叫你。",
    "ask_phrases": "你是指什麼時候？\n大概什麼時候？\n要約幾點？",
    "ask_event_phrases": "你大概幾點{event}？\n{event}大概是幾點？",
    "ask_vague_phrases": "{vague}是指什麼時候？\n{vague}大概是幾點？",
    "snooze_phrases": "好，等等再叫你。\nOK，延後一下。\n晚點再提醒你。",
    "snooze_when_phrases": "好，{when}再叫你。\nOK，{when}再說。",
    "cancel_phrases": "好，拿掉了。\n刪掉了。\nOK，處理好了。",
    "cancel_all_phrases": "全部清掉了。\n好，{scope}的提醒都取消了。",
    "note_phrases": "記起來了。\n好，我記著。\n收好了。",
    "fire_phrases": "該{text}囉。\n別忘了{text}。\n{text}的時間到了。\n你之前交代我的事來了：{text}",
    "nag_phrases": "{text}還沒弄喔？\n還記得{text}嗎？\n提醒你一下：{text}",
    "rain_advice": "帶把傘吧☔\n有曬衣服的話記得收。\n{day}別跟天空賭運氣。",
    "hot_advice": "記得補充水分。\n注意避暑。\n{day}有點曬。",
    "cold_advice": "可以帶個外套。\n{day}偏涼。\n別穿太少。",
    "nice_advice": "天氣不錯，適合出門。\n{day}很舒服。\n出去走走吧。",
    "done_phrases": "👍\n好～\n辛苦了",
    "timer_phrases": "好，{duration}後叫你。\n開始計時⏱️",
    "rain_confirm_phrases": "好，下雨天叫你。\nOK，會下雨再跟你說。",
    "payday_ask_phrases": "你發薪日是幾號？",
    "payday_set_phrases": "記住了，每月 {day} 號。",
    "draft_cancel_phrases": "好，先不設。\nOK，那先不提醒。",
    "no_target_phrases": "現在沒有在響的提醒耶。",
    "location_phrases": "好，改看{place}的天氣。\n收到，你在{place}。",
    "weather_wait_phrases": "氣象署沒回應，{minutes} 分鐘後再試一次。\n天氣抓不到，等 {minutes} 分鐘再試。",
    "alert_phrases": "{hour}點後可能{what}\n注意，{hour}點後可能{what}",
    "rain_yes_phrases": "{when}可能下雨，降雨 {pop}%。\n{when}會下雨喔，{pop}%。",
    "rain_no_phrases": "{when}應該不會下雨。\n{when}看起來不會下雨。",
    "not_found_phrases": "找不到耶。",
    "unknown_phrases": "嗯？沒聽懂😅\n這句我還不會耶。\n換個說法試試？",
    "show_exact_time": False,         # 回覆下面用小字附上實際時間（預設關；按「查看」看得到）

    # 常見提醒：講到這些就建立同一種提醒，用固定的回法（一行一種：名稱｜關鍵字｜設好時的回覆｜時間到的訊息，多個用 / 隔開）
    "topics": "休息｜睡覺/休息/午睡/去睡/睡/躺一下/放鬆/小睡｜好，我會提醒你休息。/OK，到時候叫你休息。｜該休息囉。/去休息吧。\n"
              "喝水｜喝水/喝點水/補水/喝杯水/喝口水｜好，會叫你喝水。/OK，記得喝水。｜喝口水吧💧/該喝水囉💧\n"
              "吃飯｜吃飯/吃午餐/吃晚餐/吃早餐/吃午飯/吃晚飯/吃早飯/用餐/吃東西｜好，到時候叫你吃飯。/OK，會提醒你吃飯。｜該吃飯囉🍚/去吃飯吧🍚\n"
              "起床｜起床/起來/醒來/叫醒｜好，到時候叫你起床。/OK，會叫你起來。｜起床囉☀️/該起來囉☀️\n"
              "上線｜上線/上遊戲/開遊戲/打遊戲｜好，時間到叫你上線。/OK，會提醒你上線。｜該上線囉🎮/上線時間到🎮",

    # 時間到的訊息
    "timer_template": "⏱️ {mentions} 時間到！{text}",
    "rain_template": "☔ {mentions} 今天會下雨，{text}",

    # 提醒響的時候的按鈕
    "btn_done_label": "好",
    "btn_later_label": "等等做",
    "btn_later_minutes": 90,
    "btn_tomorrow_label": "明天再說",
    "btn_tomorrow_time": "15:00",
    "btn_stop_label": "之後都取消",

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
    "weather_slot_hours": "8,12,16,20",   # 卡片上列哪幾個時段
    "weather_slot_day": "auto",      # 看哪一天：auto（第一個時段之前看今天，之後看明天）／today／tomorrow
    "weather_hot": 32,               # 最高溫幾度以上算熱
    "weather_cold": 16,              # 最低溫幾度以下算冷
    "weather_retries": 3,            # 抓天氣失敗先連試幾次
    "weather_retry_wait": 5,         # 都失敗就等幾分鐘再來一輪，還是失敗才回報
    "weather_alert_enabled": True,   # 預報變成會下雨時通知
    "weather_check_minutes": 40,
    "weather_alert_hours": 12,       # 看未來幾小時
    "weather_quiet": "23:00-07:00",  # 這段時間不通知（等結束後再說）

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
    # 記下願望時的回覆：一行一個清單「清單名稱：回覆/回覆」，「*」是其他清單
    "wish_acks": "想看：要看/支持/屁眼\n想買：買/要買/支持\n想畫：畫！/要畫/支持\n想學：學起來/支持\n"
                 "想去：去！/走/支持\n想吃：吃/要吃/支持\n想做：做！/支持\n*：我記住了/好～/記下來了",
    "suggest_count": 5,
    "suggest_half_life_days": 14,    # 越近期提到的越常被挑到；過了這麼多天，加成剩一半
    "suggest_phrases": "可以{verb}這些：\n挑了幾個：\n試試這些？",

    # 記事分類（說「記住…」「記一下…」時用）：一行一類，最後一行是沒對到時的預設分類
    "note_categories": "靈感：靈感,點子,想法,idea,突然想到\n"
                       "主題：主題,題目\n"
                       "備忘：",
}

# 設定網頁、測試網頁共用的欄位名稱
LABELS = {
    "feature_reminder": "提醒", "feature_note": "願望清單、記事", "feature_timer": "計時",
    "feature_weather": "天氣", "feature_mention_others": "提醒別人",
    "all_channels": "所有頻道都回應（提醒發到提醒頻道）",
    "confirm_phrases": "一般", "confirm_when_phrases": "照著講時間 {when}", "confirm_other_phrases": "提醒別人 {who}",
    "repeat_phrases": "重複提醒", "ask_phrases": "一般", "ask_event_phrases": "看事件 {event}",
    "ask_vague_phrases": "太模糊 {vague}", "snooze_phrases": "一般", "snooze_when_phrases": "照著講時間 {when}",
    "cancel_phrases": "一個", "cancel_all_phrases": "一整批 {scope}", "done_phrases": "回覆「好」",
    "note_phrases": "記事", "wish_acks": "願望（清單：回覆/回覆）", "suggest_phrases": "挑清單 {verb}",
    "fire_phrases": "時間到 {text}", "nag_phrases": "再催 {text}", "timer_template": "計時到 {mentions} {text}",
    "rain_template": "下雨天 {mentions} {text}", "timer_phrases": "開始計時 {duration}",
    "rain_confirm_phrases": "設好下雨天提醒", "payday_ask_phrases": "問發薪日", "payday_set_phrases": "記住發薪日 {day}",
    "draft_cancel_phrases": "不設了", "no_target_phrases": "沒有可延後的", "not_found_phrases": "找不到",
    "unknown_phrases": "聽不懂",
    "rain_advice": "叮嚀：下雨 {day}", "hot_advice": "叮嚀：很熱 {day}", "cold_advice": "叮嚀：偏冷 {day}", "nice_advice": "叮嚀：舒服 {day}",
    "topics": "名稱｜關鍵字｜回覆｜時間到（多個用 / 隔開）",
    "show_exact_time": "回覆附上實際時間",
    "btn_done_label": "完成", "btn_later_label": "晚點", "btn_later_minutes": "晚點＝幾分鐘後",
    "btn_tomorrow_label": "明天", "btn_tomorrow_time": "明天＝幾點", "btn_stop_label": "之後都取消",
    "date_only_time": "只講日期", "time_morning": "早上", "time_noon": "中午",
    "time_afternoon": "下午", "time_evening": "傍晚", "time_night": "晚上",
    "few_days": "這幾天＝幾天後", "payday": "發薪日（0＝沒設）",
    "nag_enabled": "沒按「好」會再催", "nag_interval_minutes": "間隔（分鐘）", "nag_max": "最多幾次",
    "weather_daily_enabled": "每天貼天氣", "weather_daily_time": "幾點", "weather_county": "縣市",
    "weather_rain_threshold": "降雨機率 % 算下雨",
    "weather_slot_hours": "卡片時段（逗號分隔）", "weather_slot_day": "看哪天（auto／today／tomorrow）",
    "weather_hot": "幾度以上算熱", "weather_cold": "幾度以下算冷",
    "weather_retries": "失敗先連試幾次", "weather_retry_wait": "都失敗等幾分鐘再試",
    "weather_wait_phrases": "抓不到時先說一聲",
    "weather_alert_enabled": "預報變成會下雨時通知", "weather_check_minutes": "幾分鐘檢查一次",
    "weather_alert_hours": "看未來幾小時", "weather_quiet": "不通知的時段",
    "location_phrases": "改地點 {place}", "alert_phrases": "天氣變化 {hour} {what}",
    "rain_yes_phrases": "會下雨嗎→會 {when} {pop}", "rain_no_phrases": "會下雨嗎→不會 {when}",
    "note_categories": "記事分類（分類：關鍵字,關鍵字）", "wish_lists": "願望清單（清單：動詞,動詞）",
    "wish_listen_ids": "沒 @ 也聽誰（ID，空白＝只有你）", "passive_ack": "沒 @ 時回一句",
    "suggest_count": "一次挑幾個", "suggest_half_life_days": "近期加成半衰期（天）",
}
HINTS: dict = {}
# 設定頁
GROUPS = [
    ("功能", ["feature_reminder", "feature_note", "feature_timer", "feature_weather", "feature_mention_others",
            "all_channels"]),
    ("按鈕", ["btn_done_label", "btn_later_label", "btn_later_minutes", "btn_tomorrow_label", "btn_tomorrow_time",
            "btn_stop_label"]),
    ("時間", ["date_only_time", "time_morning", "time_noon", "time_afternoon", "time_evening", "time_night",
            "few_days", "payday"]),
    ("再催", ["nag_enabled", "nag_interval_minutes", "nag_max"]),
    ("天氣", ["weather_daily_enabled", "weather_daily_time", "weather_county", "weather_rain_threshold",
            "weather_slot_hours", "weather_slot_day", "weather_hot", "weather_cold", "weather_retries",
            "weather_retry_wait"]),
    ("天氣變化通知", ["weather_alert_enabled", "weather_check_minutes", "weather_alert_hours", "weather_quiet"]),
    ("清單", ["wish_lists", "wish_listen_ids", "passive_ack", "suggest_count", "suggest_half_life_days",
            "note_categories"]),
    ("其他", ["show_exact_time"]),
]
# 回覆頁：bot 會說的每一句話
REPLY_GROUPS = [
    ("設好提醒", ["confirm_phrases", "confirm_when_phrases", "confirm_other_phrases", "repeat_phrases",
              "rain_confirm_phrases", "timer_phrases"]),
    ("時間到", ["fire_phrases", "nag_phrases", "timer_template", "rain_template"]),
    ("問時間", ["ask_phrases", "ask_event_phrases", "ask_vague_phrases", "payday_ask_phrases"]),
    ("延後", ["snooze_phrases", "snooze_when_phrases", "no_target_phrases"]),
    ("取消／完成", ["cancel_phrases", "cancel_all_phrases", "done_phrases", "draft_cancel_phrases", "not_found_phrases"]),
    ("清單", ["wish_acks", "note_phrases", "suggest_phrases", "payday_set_phrases"]),
    ("天氣", ["rain_advice", "hot_advice", "cold_advice", "nice_advice", "rain_yes_phrases", "rain_no_phrases",
            "alert_phrases", "location_phrases", "weather_wait_phrases"]),
    ("常見提醒", ["topics"]),
    ("其他", ["unknown_phrases"]),
]
REPLY_KEYS = [k for _, keys in REPLY_GROUPS for k in keys]
SETTING_KEYS = [k for _, keys in GROUPS for k in keys]
MULTILINE_KEYS = [k for k, v in DEFAULTS.items() if isinstance(v, str) and "\n" in v] + \
    [k for k in REPLY_KEYS if k not in ("timer_template", "rain_template")]
MULTILINE_KEYS = list(dict.fromkeys(MULTILINE_KEYS))

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


def wish_acks(settings: dict, category: str) -> list[str]:
    table = {}
    for line in str(settings["wish_acks"]).splitlines():
        name, _, rest = line.partition("：") if "：" in line else line.partition(":")
        table[name.strip()] = [x.strip() for x in rest.split("/") if x.strip()]
    return table.get(category) or table.get("*") or ["我記住了"]


@dataclass
class Topic:
    name: str
    keywords: list[str]
    confirm: list[str]
    fire: list[str]


def topics(settings: dict) -> list[Topic]:
    out = []
    for line in str(settings.get("topics", "")).splitlines():
        parts = [p.strip() for p in re.split(r"[｜|]", line)]
        if len(parts) < 2 or not parts[0]:
            continue
        split = lambda x: [i.strip() for i in x.split("/") if i.strip()]  # noqa: E731
        out.append(Topic(parts[0], split(parts[1]), split(parts[2]) if len(parts) > 2 else [],
                         split(parts[3]) if len(parts) > 3 else []))
    return out


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


def validate(form: dict, keys=None) -> tuple[dict, list[str]]:
    """把網頁表單轉成設定值，回傳 (新設定, 錯誤訊息)。只處理 keys（預設全部），其他設定不動。"""
    errors, out = [], {}
    for k in (keys or DEFAULTS):
        default = DEFAULTS[k]
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
        elif k == "weather_slot_day":
            out[k] = raw if raw in ("auto", "today", "tomorrow") else default
        elif k == "weather_slot_hours":
            hours = [x for x in re.split(r"[,，\s]+", raw) if x.isdigit() and 0 <= int(x) <= 23]
            out[k] = ",".join(hours) if hours else default
        elif k == "weather_quiet":
            out[k] = raw if re.fullmatch(r"\d{1,2}:\d{2}-\d{1,2}:\d{2}", raw) or raw == "" else default
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
