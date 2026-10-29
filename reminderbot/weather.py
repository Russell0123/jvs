"""中央氣象署開放資料：一般天氣預報－今明 36 小時天氣預報（F-C0032-001）。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

API = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001"


@dataclass
class Period:
    start: datetime
    end: datetime
    wx: str          # 天氣現象
    pop: int         # 降雨機率 %
    min_t: int
    max_t: int
    ci: str          # 舒適度

    def label(self, now: datetime) -> str:
        diff = (self.start.date() - now.astimezone(self.start.tzinfo).date()).days
        day = {0: "今天", 1: "明天", 2: "後天"}.get(diff, f"{self.start:%m/%d}")
        h = self.start.hour
        return f"{day}{'白天' if 6 <= h < 18 else '晚上' if h >= 18 else '凌晨'}"


class WeatherError(Exception):
    pass


async def fetch(api_key: str, county: str, tz) -> list[Period]:
    import aiohttp  # 測試網頁（瀏覽器）沒有 aiohttp，所以用到才載入
    if not api_key:
        raise WeatherError("還沒設定中央氣象署的 API key（CWA_API_KEY）")
    params = {"Authorization": api_key, "locationName": county, "format": "JSON"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.get(API, params=params) as r:
                if r.status != 200:
                    raise WeatherError(f"氣象署回應 {r.status}")
                data = await r.json(content_type=None)
    except aiohttp.ClientError as e:
        raise WeatherError(f"連不到氣象署：{e}") from e
    return parse(data, county, tz)


def parse(data: dict, county: str, tz) -> list[Period]:
    try:
        locs = data["records"]["location"]
        loc = next(l for l in locs if l["locationName"] == county)
    except (KeyError, StopIteration, TypeError) as e:
        raise WeatherError(f"氣象署資料裡找不到 {county}") from e

    elements = {e["elementName"]: e["time"] for e in loc["weatherElement"]}
    periods = []
    for i, t in enumerate(elements.get("Wx", [])):
        def val(name, default=""):
            try:
                return elements[name][i]["parameter"]["parameterName"]
            except (KeyError, IndexError):
                return default
        periods.append(Period(
            start=_dt(t["startTime"], tz), end=_dt(t["endTime"], tz),
            wx=val("Wx"), pop=int(val("PoP", "0") or 0),
            min_t=int(val("MinT", "0") or 0), max_t=int(val("MaxT", "0") or 0),
            ci=val("CI")))
    if not periods:
        raise WeatherError("氣象署資料是空的")
    return periods


def _dt(s: str, tz) -> datetime:
    return datetime.fromisoformat(s.replace(" ", "T")).replace(tzinfo=None).replace(tzinfo=tz)


def format_report(county: str, periods: list[Period], now: datetime, advice: str) -> str:
    """短短幾行預報，最後一定是一句行動建議。"""
    lines = [f"🌤️ **{county}**"]
    for p in periods[:3]:
        lines.append(f"・{p.label(now)} {p.wx} {p.min_t}–{p.max_t}°C ☔{p.pop}%")
    lines.append(advice)
    lines.append("-# 中央氣象署")
    return "\n".join(lines)
