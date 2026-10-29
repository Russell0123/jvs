"""中央氣象署開放資料。

用到三份資料：
- 現在天氣：O-A0003-001（局屬氣象站即時觀測），挑使用者所在縣市的測站
- 逐 3 小時預報：F-D0047-0XX（各縣市鄉鎮未來 3 天），挑使用者所在的鄉鎮區
- 一週預報：F-D0047-0XX+2（各縣市鄉鎮未來 1 週，逐 12 小時）

氣象署在 2024 年改過 JSON 格式（欄位名稱改成中文、大小寫不同），這裡兩種都吃。
網路的部分（fetch_bundle）只有正式 bot 會用；測試網頁用 demo_bundle 產生假資料。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

BASE = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/"
CURRENT_ID = "O-A0003-001"

# 各縣市鄉鎮未來 3 天逐 3 小時預報的資料編號；一週預報 = 這個編號 + 2
FORECAST_IDS = {
    "宜蘭縣": 1, "桃園市": 5, "新竹縣": 9, "苗栗縣": 13, "彰化縣": 17, "南投縣": 21, "雲林縣": 25,
    "嘉義縣": 29, "屏東縣": 33, "臺東縣": 37, "花蓮縣": 41, "澎湖縣": 45, "基隆市": 49, "新竹市": 53,
    "嘉義市": 57, "臺北市": 61, "高雄市": 65, "新北市": 69, "臺中市": 73, "臺南市": 77, "連江縣": 81,
    "金門縣": 85,
}
# 沒講鄉鎮區時用縣市政府所在地
DEFAULT_TOWNS = {
    "臺北市": "信義區", "新北市": "板橋區", "桃園市": "桃園區", "臺中市": "西屯區", "臺南市": "安平區",
    "高雄市": "苓雅區", "基隆市": "中正區", "新竹市": "北區", "嘉義市": "東區", "新竹縣": "竹北市",
    "苗栗縣": "苗栗市", "彰化縣": "彰化市", "南投縣": "南投市", "雲林縣": "斗六市", "嘉義縣": "太保市",
    "屏東縣": "屏東市", "宜蘭縣": "宜蘭市", "花蓮縣": "花蓮市", "臺東縣": "臺東市", "澎湖縣": "馬公市",
    "金門縣": "金城鎮", "連江縣": "南竿鄉",
}


def dataset_id(county: str, week: bool = False) -> str:
    n = FORECAST_IDS[county] + (2 if week else 0)
    return f"F-D0047-{n:03d}"


@dataclass
class Current:
    station: str
    time: datetime | None
    wx: str
    temp: float | None


@dataclass
class Slot:
    start: datetime
    end: datetime
    wx: str = ""
    temp: float | None = None
    pop: int | None = None
    min_t: float | None = None
    max_t: float | None = None


@dataclass
class Bundle:
    county: str
    town: str
    current: Current | None = None
    slots: list[Slot] = field(default_factory=list)     # 逐 3 小時
    week: list[Slot] = field(default_factory=list)      # 逐 12 小時
    errors: list[str] = field(default_factory=list)

    @property
    def place(self) -> str:
        return f"{self.county}{self.town}"


class WeatherError(Exception):
    pass


# ---------- 抓資料（正式 bot） ----------

async def fetch_bundle(api_key: str, county: str, town: str, tz, need_current=True, need_week=False) -> Bundle:
    import aiohttp  # 測試網頁（瀏覽器）沒有 aiohttp，所以用到才載入
    if not api_key:
        raise WeatherError("還沒設定氣象署的 API key（CWA_API_KEY）")
    if county not in FORECAST_IDS:
        raise WeatherError(f"不認得「{county}」")
    town = town or DEFAULT_TOWNS.get(county, "")
    b = Bundle(county, town)

    async def get(dataset: str, **params) -> dict:
        async with session.get(BASE + dataset, params={"Authorization": api_key, "format": "JSON", **params}) as r:
            if r.status != 200:
                raise WeatherError(f"氣象署回應 {r.status}（{dataset}）")
            return await r.json(content_type=None)

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            try:
                b.slots = parse_forecast(await get(dataset_id(county)), town, tz)
            except (WeatherError, KeyError, TypeError, ValueError) as e:
                b.errors.append(f"預報：{e}")
            if need_current:
                try:
                    b.current = parse_current(await get(CURRENT_ID), county, tz)
                except (WeatherError, KeyError, TypeError, ValueError) as e:
                    b.errors.append(f"即時觀測：{e}")
            if need_week:
                try:
                    b.week = parse_forecast(await get(dataset_id(county, week=True)), town, tz)
                except (WeatherError, KeyError, TypeError, ValueError) as e:
                    b.errors.append(f"一週預報：{e}")
    except aiohttp.ClientError as e:
        raise WeatherError(f"連不到氣象署：{e}") from e
    if not b.slots and not b.week and not b.current:
        raise WeatherError("；".join(b.errors) or "氣象署沒有資料")
    return b


# ---------- 解析（兩種格式都吃） ----------

def _get(d: dict, *keys, default=None):
    for k in keys:
        if isinstance(d, dict) and k in d:
            return d[k]
    return default


def _dt(s: str, tz) -> datetime:
    s = s.replace(" ", "T")
    d = datetime.fromisoformat(s)
    return d.astimezone(tz) if d.tzinfo else d.replace(tzinfo=tz)


def _num(v):
    try:
        return float(re.findall(r"-?\d+(?:\.\d+)?", str(v))[0])
    except (IndexError, ValueError):
        return None


ELEMENTS = {
    "wx": ("天氣現象", "Wx"),
    "temp": ("溫度", "T", "平均溫度"),
    "pop": ("3小時降雨機率", "6小時降雨機率", "12小時降雨機率", "PoP3h", "PoP6h", "PoP12h"),
    "max_t": ("最高溫度", "MaxT"),
    "min_t": ("最低溫度", "MinT"),
}


def _value(entry: dict, kind: str):
    vals = _get(entry, "ElementValue", "elementValue", default=[]) or []
    if not vals:
        return None
    v = vals[0]
    if kind == "wx":
        return str(_get(v, "Weather", "value", default=""))
    # 新格式：{"Temperature": "23"}；舊格式：{"value": "23", "measures": "..."}
    raw = next((x for k, x in v.items() if "Code" not in k and k != "measures"), None) if isinstance(v, dict) else v
    return _num(raw)


def parse_forecast(data: dict, town: str, tz) -> list[Slot]:
    rec = data["records"]
    group = _get(rec, "Locations", "locations")[0]
    locs = _get(group, "Location", "location")
    names = [_get(x, "LocationName", "locationName") or "" for x in locs]
    i = next((i for i, n in enumerate(names) if n == town), None)
    if i is None and town:
        i = next((i for i, n in enumerate(names) if n.startswith(town)), None)
    loc = locs[i or 0]
    ranges: dict[str, list[tuple[datetime, datetime, object]]] = {}
    points: dict[str, list[tuple[datetime, object]]] = {}
    for el in _get(loc, "WeatherElement", "weatherElement"):
        name = _get(el, "ElementName", "elementName")
        kind = next((k for k, names in ELEMENTS.items() if name in names), None)
        if not kind:
            continue
        for t in _get(el, "Time", "time"):
            value = _value(t, kind)
            start = _get(t, "StartTime", "startTime")
            if start is None:      # 溫度是「某個時間點」的值
                points.setdefault(kind, []).append((_dt(_get(t, "DataTime", "dataTime"), tz), value))
            else:
                ranges.setdefault(kind, []).append((_dt(start, tz), _dt(_get(t, "EndTime", "endTime"), tz), value))
    # 時段照「天氣現象」切；其他欄位（降雨機率可能是 6 小時一段）填進涵蓋它的時段
    base = ranges.get("wx") or ranges.get("pop") or ranges.get("temp") or []
    out = []
    for st, en, _ in sorted(base, key=lambda r: r[0]):
        slot = Slot(st, en)
        for kind in ("wx", "temp", "pop", "max_t", "min_t"):
            v = next((v for a, b, v in ranges.get(kind, []) if a <= st < b), None)
            if v is None:
                v = next((v for at, v in points.get(kind, []) if st <= at < en), None)
            if v is not None:
                setattr(slot, kind, int(v) if kind == "pop" else v)
        out.append(slot)
    if not out:
        raise WeatherError("預報是空的")
    return out


def parse_current(data: dict, county: str, tz) -> Current:
    stations = _get(data["records"], "Station", "station")
    short = county[:2]
    mine = [s for s in stations if _get(_get(s, "GeoInfo", default={}), "CountyName") == county]
    if not mine:
        raise WeatherError(f"{county}沒有測站資料")
    st = next((s for s in mine if _get(s, "StationName", "") in (short, county)), mine[0])
    we = _get(st, "WeatherElement", default={})
    temp = _num(_get(we, "AirTemperature"))
    wx = str(_get(we, "Weather", default="") or "")
    if wx in ("-99", "None"):
        wx = ""
    t = _get(_get(st, "ObsTime", default={}), "DateTime")
    return Current(_get(st, "StationName", default=""), _dt(t, tz) if t else None, wx,
                   temp if temp is not None and temp > -50 else None)


# ---------- 整理 ----------

def icon(wx: str) -> str:
    if not wx:
        return "🌡️"
    for keys, ic in (("雷", "⛈️"), ("雪", "❄️"), ("雨", "🌧️"), ("霧", "🌫️"), ("陰", "☁️"), ("多雲", "⛅"), ("晴", "☀️")):
        if any(k in wx for k in keys.split("|")):
            return ic
    return "🌤️"


def slot_at(slots: list[Slot], at: datetime) -> Slot | None:
    for s in slots:
        if s.start <= at < s.end:
            return s
    return None


def day_range(slots: list[Slot], day) -> tuple[float, float] | None:
    temps = [v for s in slots if s.start.date() == day for v in (s.temp, s.min_t, s.max_t) if v is not None]
    return (min(temps), max(temps)) if temps else None


def is_rain(s: Slot, threshold: int) -> bool:
    return (s.pop or 0) >= threshold or "雨" in s.wx or "雷" in s.wx


SPECIAL = ("雷", "豪雨", "大雨", "冰雹", "雪", "強風", "颱風")


def special(s: Slot) -> str:
    return next((k for k in SPECIAL if k in s.wx), "")


def week_days(slots: list[Slot]) -> list[tuple[object, str, float | None, float | None, int | None]]:
    """一週預報整理成每天一行：(日期, 白天天氣, 最低, 最高, 最高降雨機率)。"""
    days: dict = {}
    for s in slots:
        d = s.start.date()
        e = days.setdefault(d, {"wx": "", "lo": None, "hi": None, "pop": None})
        if 6 <= s.start.hour < 18 or not e["wx"]:
            e["wx"] = s.wx or e["wx"]
        for v in (s.temp, s.min_t, s.max_t):
            if v is not None:
                e["lo"] = v if e["lo"] is None else min(e["lo"], v)
                e["hi"] = v if e["hi"] is None else max(e["hi"], v)
        if s.pop is not None:
            e["pop"] = s.pop if e["pop"] is None else max(e["pop"], s.pop)
    return [(d, e["wx"], e["lo"], e["hi"], e["pop"]) for d, e in sorted(days.items())]


# ---------- 測試網頁、測試用的假資料 ----------

def demo_bundle(now: datetime, county: str, town: str = "", rain_from: int | None = 15,
                week: bool = False) -> Bundle:
    """假資料：rain_from 點之後轉雨（None = 都不下雨）。"""
    town = town or DEFAULT_TOWNS.get(county, "")
    start = now.replace(minute=0, second=0, microsecond=0)
    start -= timedelta(hours=start.hour % 3)
    slots = []
    for i in range(24):
        st = start + timedelta(hours=3 * i)
        h = st.hour
        rainy = rain_from is not None and st >= now.replace(hour=0, minute=0) + timedelta(hours=rain_from) \
            and st < now.replace(hour=0, minute=0) + timedelta(hours=rain_from + 18)
        temp = 22 + (5 if 9 <= h <= 15 else 2 if 6 <= h <= 18 else 0) - (2 if rainy else 0)
        slots.append(Slot(st, st + timedelta(hours=3), "短暫陣雨" if rainy else ("晴" if 6 <= h < 18 else "多雲"),
                          float(temp), 70 if rainy else 10))
    b = Bundle(county, town, Current(county[:2], now, "多雲", 24.0), slots)
    if week:
        d0 = now.replace(hour=6, minute=0, second=0, microsecond=0)
        for i in range(14):
            st = d0 + timedelta(hours=12 * i)
            rainy = i in (4, 5, 9)
            b.week.append(Slot(st, st + timedelta(hours=12), "陣雨" if rainy else "多雲時晴", None,
                               60 if rainy else 20, 19.0 + (i % 3), 26.0 + (i % 4)))
    return b
