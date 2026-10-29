"""在主機上檢查氣象署資料抓不抓得到：

    python -m reminderbot.weathercheck            # 用設定裡的縣市
    python -m reminderbot.weathercheck 臺中市 西屯區

會印出抓到的現在天氣、逐 3 小時預報、一週預報；抓不到時印出氣象署回的前幾百個字，方便除錯。
"""
import asyncio
import os
import sys
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from . import weather


async def main():
    tz = ZoneInfo("Asia/Taipei")
    key = os.environ.get("CWA_API_KEY", "")
    county = sys.argv[1].replace("台", "臺") if len(sys.argv) > 1 else "臺北市"
    town = sys.argv[2] if len(sys.argv) > 2 else ""
    print(f"縣市：{county}　鄉鎮：{town or weather.DEFAULT_TOWNS.get(county)}　key：{'有' if key else '沒有！'}")
    try:
        b = await weather.fetch_bundle(key, county, town, tz, need_week=True)
    except weather.WeatherError as e:
        print("❌", e)
        await raw(key, county)
        return
    c = b.current
    print("✅ 現在：", f"{c.station}站 {c.time} {c.wx} {c.temp}°C" if c else "（沒抓到）")
    print(f"✅ 逐 3 小時：{len(b.slots)} 段")
    for x in b.slots[:8]:
        print(f"   {x.start:%m/%d %H:%M} {x.wx} {x.temp}°C ☔{x.pop}%")
    print(f"✅ 一週：{len(b.week)} 段")
    for d, wx, lo, hi, pop in weather.week_days(b.week):
        print(f"   {d} {wx} {lo}–{hi}°C ☔{pop}%")
    if b.errors:
        print("⚠️ 部分沒抓到：", b.errors)
        await raw(key, county)


async def raw(key, county):
    import aiohttp
    async with aiohttp.ClientSession() as s:
        for ds in (weather.dataset_id(county), weather.CURRENT_ID):
            async with s.get(weather.BASE + ds, params={"Authorization": key, "format": "JSON"}) as r:
                text = await r.text()
                print(f"--- {ds}（HTTP {r.status}）前 600 字 ---\n{text[:600]}")


if __name__ == "__main__":
    asyncio.run(main())
