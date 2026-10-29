"""設定網頁：只綁 127.0.0.1，外面透過 Cloudflare Tunnel + Access（或 SSH 轉發）才連得到。"""
from __future__ import annotations

import base64
import hmac
import logging
from datetime import datetime
from html import escape
from urllib.parse import urlparse

from aiohttp import web

from . import timeparse
from .settings import COUNTIES, DEFAULTS, validate

log = logging.getLogger(__name__)

LABELS = {
    "feature_reminder": "提醒", "feature_note": "記住（靈感／想看／備忘）", "feature_timer": "計時器",
    "feature_weather": "天氣", "feature_mention_others": "可以提醒別人（@其他成員）",
    "confirm_template": "設定成功的回覆", "reminder_template": "提醒時間到的訊息",
    "nag_template": "再次提醒的訊息", "timer_template": "計時到的訊息", "note_saved_template": "記事存好的回覆",
    "date_only_time": "只講日期時用幾點", "time_morning": "「早上」= 幾點（也是延後到明天的時間）",
    "time_noon": "「中午」", "time_afternoon": "「下午」", "time_evening": "「傍晚」", "time_night": "「晚上」",
    "snooze_minutes": "延後按鈕（分鐘，逗號分隔）", "snooze_tomorrow": "有「明天」延後按鈕",
    "nag_enabled": "沒按完成會再提醒", "nag_interval_minutes": "再提醒間隔（分鐘）", "nag_max": "最多再提醒幾次",
    "weather_daily_enabled": "每天自動貼天氣", "weather_daily_time": "每天幾點貼", "weather_county": "縣市",
    "weather_rain_threshold": "降雨機率超過幾 % 提醒帶傘",
    "note_categories": "記事分類（一行一類：「分類：關鍵字,關鍵字」，最後一行是沒對到時的預設分類）",
}
HINTS = {
    "confirm_template": "{id} {time} {repeat} {text} {targets}",
    "reminder_template": "{mentions} {text} {time} {creator} {repeat}",
    "nag_template": "{mentions} {text} {count}",
    "timer_template": "{mentions} {duration} {text}",
    "note_saved_template": "{id} {category} {text}",
}
GROUPS = [
    ("功能開關", ["feature_reminder", "feature_note", "feature_timer", "feature_weather", "feature_mention_others"]),
    ("訊息格式", ["confirm_template", "reminder_template", "nag_template", "timer_template", "note_saved_template"]),
    ("時間", ["date_only_time", "time_morning", "time_noon", "time_afternoon", "time_evening", "time_night"]),
    ("延後與再提醒", ["snooze_minutes", "snooze_tomorrow", "nag_enabled", "nag_interval_minutes", "nag_max"]),
    ("天氣", ["weather_daily_enabled", "weather_daily_time", "weather_county", "weather_rain_threshold"]),
    ("記事", ["note_categories"]),
]
STATUS = {"pending": "等待中", "fired": "已響・未完成", "done": "已完成", "cancelled": "已取消"}

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#1d2330;--muted:#6b7280;--line:#e3e6eb;--accent:#5865f2;--danger:#d14343}
@media (prefers-color-scheme:dark){:root{--bg:#16181d;--card:#1f2229;--fg:#e6e8ec;--muted:#9aa1ad;--line:#2e323b;--accent:#7b85ff;--danger:#ff6b6b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,-apple-system,"Noto Sans TC","PingFang TC",sans-serif}
header{background:var(--card);border-bottom:1px solid var(--line);padding:12px 16px;display:flex;gap:16px;align-items:center;flex-wrap:wrap}
header b{font-size:17px}nav a{color:var(--muted);text-decoration:none;margin-right:14px}nav a.on{color:var(--accent);font-weight:600}
main{max-width:860px;margin:0 auto;padding:16px}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:16px}
h2{font-size:16px;margin:0 0 12px}label{display:block;margin:10px 0 4px;color:var(--muted);font-size:13px}
input[type=text],input[type=number],select,textarea{width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);font:inherit}
textarea{min-height:96px}.check{display:flex;gap:8px;align-items:center;margin:8px 0;color:var(--fg);font-size:15px}
.hint{color:var(--muted);font-size:12px;margin-top:2px}
button{background:var(--accent);color:#fff;border:0;border-radius:6px;padding:9px 16px;font:inherit;cursor:pointer}
button.small{padding:4px 10px;font-size:13px}button.danger{background:var(--danger)}button.ghost{background:transparent;color:var(--accent);border:1px solid var(--accent)}
.bar{position:sticky;bottom:0;background:var(--bg);padding:12px 0;display:flex;gap:10px}
.flash{padding:10px 14px;border-radius:8px;margin-bottom:16px;background:color-mix(in srgb,var(--accent) 15%,transparent)}
.flash.err{background:color-mix(in srgb,var(--danger) 18%,transparent)}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:8px 6px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:500;white-space:nowrap}.wrap table{min-width:600px}td:first-child,td:nth-child(2){white-space:nowrap}.tag{font-size:12px;color:var(--muted)}.wrap{overflow-x:auto}
.filters a{margin-right:12px;color:var(--muted)}.filters a.on{color:var(--accent);font-weight:600}
"""


def page(title: str, active: str, body: str, flash: str = "", error: bool = False) -> web.Response:
    nav = "".join(f'<a href="{href}" class="{"on" if key == active else ""}">{name}</a>'
                  for key, href, name in [("settings", "/", "設定"), ("reminders", "/reminders", "提醒"),
                                          ("notes", "/notes", "記事")])
    fl = f'<div class="flash{" err" if error else ""}">{flash}</div>' if flash else ""
    html = (f"<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'><title>{escape(title)}</title>"
            f"<style>{CSS}</style></head><body><header><b>🤖 助理設定</b><nav>{nav}</nav></header>"
            f"<main>{fl}{body}</main></body></html>")
    return web.Response(text=html, content_type="text/html")


def make_app(bot) -> web.Application:
    cfg = bot.config

    @web.middleware
    async def guard(request: web.Request, handler):
        if cfg.admin_email:
            email = request.headers.get("Cf-Access-Authenticated-User-Email", "").lower()
            if email != cfg.admin_email:
                return web.Response(status=403, text="403：只有管理員可以進來（Cloudflare Access 驗證未通過）")
        if cfg.web_password:
            auth = request.headers.get("Authorization", "")
            ok = False
            if auth.startswith("Basic "):
                try:
                    user, _, pw = base64.b64decode(auth[6:]).decode().partition(":")
                    ok = user == "admin" and hmac.compare_digest(pw, cfg.web_password)
                except Exception:
                    ok = False
            if not ok:
                return web.Response(status=401, headers={"WWW-Authenticate": 'Basic realm="bot"'})
        if request.method == "POST":
            # 擋掉從別的網站送過來的表單
            origin = request.headers.get("Origin") or request.headers.get("Referer") or ""
            if origin and urlparse(origin).netloc != request.host:
                return web.Response(status=403, text="403：來源不符")
        return await handler(request)

    async def names(ids) -> dict[int, str]:
        out = {}
        for i in set(ids):
            u = bot.get_user(i)
            if u is None:
                try:
                    u = await bot.fetch_user(i)
                except Exception:
                    u = None
            out[i] = u.display_name if u else str(i)
        return out

    async def settings_page(request: web.Request):
        s = await bot.db.settings()
        msg = {"saved": "✅ 已儲存，馬上生效。", "weather": "✅ 已把天氣貼到頻道。"}.get(request.query.get("ok", ""), "")
        return page("助理設定", "settings", render_settings(s), msg)

    async def settings_save(request: web.Request):
        form = await request.post()
        values, errors = validate(dict(form))
        await bot.db.save_settings(values)
        if errors:
            return page("助理設定", "settings", render_settings(await bot.db.settings()),
                        "部分欄位沒存：" + escape("；".join(errors)), error=True)
        raise web.HTTPSeeOther("/?ok=saved")

    async def settings_reset(request: web.Request):
        await bot.db.save_settings(DEFAULTS)
        raise web.HTTPSeeOther("/?ok=saved")

    async def weather_test(request: web.Request):
        try:
            await bot.daily_weather(force=True)
        except Exception as e:
            return page("助理設定", "settings", render_settings(await bot.db.settings()),
                        "天氣發送失敗：" + escape(str(e)), error=True)
        raise web.HTTPSeeOther("/?ok=weather")

    async def reminders_page(request: web.Request):
        f = request.query.get("f", "active")
        statuses = {"active": ("pending", "fired"), "done": ("done", "cancelled"),
                    "all": ("pending", "fired", "done", "cancelled")}.get(f, ("pending", "fired"))
        rems = await bot.db.all_reminders(statuses)
        who = await names([r["creator_id"] for r in rems] +
                          [int(t[2:]) for r in rems for t in r["targets"] if t.startswith("u:")])
        rows = []
        for r in rems:
            when = timeparse.fmt_dt(datetime.fromtimestamp(r["due_at"], bot.tz), bot.tz)
            targets = "、".join(who.get(int(t[2:]), t) if t.startswith("u:") else f"身分組 {t[2:]}"
                               for t in r["targets"])
            rep = timeparse.describe_repeat(r["repeat"])
            kind = "⏱️ 計時" if r["kind"] == "timer" else ("🔁 " + rep if rep else "提醒")
            action = (f"<form method='post' action='/reminders/{r['id']}/cancel'>"
                      f"<button class='small danger'>取消</button></form>"
                      if r["status"] in ("pending", "fired") else "")
            rows.append(f"<tr><td>#{r['id']}</td><td>{when}</td><td>{escape(r['text'])}"
                        f"<div class='tag'>{kind}</div></td><td>{escape(who.get(r['creator_id'], ''))}</td>"
                        f"<td>{escape(targets)}</td><td>{STATUS.get(r['status'], r['status'])}</td><td>{action}</td></tr>")
        filt = "".join(f'<a href="?f={k}" class="{"on" if k == f else ""}">{n}</a>'
                       for k, n in [("active", "進行中"), ("done", "已結束"), ("all", "全部")])
        body = (f"<section><h2>提醒</h2><div class='filters'>{filt}</div><div class='wrap'><table>"
                f"<tr><th>#</th><th>時間</th><th>內容</th><th>建立者</th><th>提醒誰</th><th>狀態</th><th></th></tr>"
                f"{''.join(rows) or '<tr><td colspan=7>沒有資料</td></tr>'}</table></div></section>")
        return page("提醒", "reminders", body)

    async def reminder_cancel(request: web.Request):
        await bot.db.update_reminder(int(request.match_info["id"]), status="cancelled")
        raise web.HTTPSeeOther("/reminders")

    async def notes_page(request: web.Request):
        notes = await bot.db.all_notes()
        who = await names([n["user_id"] for n in notes])
        rows = "".join(
            f"<tr><td>#{n['id']}</td><td>{escape(n['category'])}</td><td>{escape(n['text'])}</td>"
            f"<td>{escape(who.get(n['user_id'], ''))}</td>"
            f"<td>{datetime.fromtimestamp(n['created_at'], bot.tz):%m/%d %H:%M}</td>"
            f"<td><form method='post' action='/notes/{n['id']}/delete'><button class='small danger'>刪除</button></form></td></tr>"
            for n in notes)
        body = (f"<section><h2>記事</h2><div class='wrap'><table><tr><th>#</th><th>分類</th><th>內容</th>"
                f"<th>誰的</th><th>時間</th><th></th></tr>{rows or '<tr><td colspan=6>沒有資料</td></tr>'}"
                f"</table></div></section>")
        return page("記事", "notes", body)

    async def note_delete(request: web.Request):
        await bot.db.delete_note(int(request.match_info["id"]))
        raise web.HTTPSeeOther("/notes")

    async def health(request: web.Request):
        return web.json_response({"ok": True, "discord_ready": bot.is_ready()})

    app = web.Application(middlewares=[guard])
    app.add_routes([
        web.get("/", settings_page), web.post("/settings", settings_save), web.post("/settings/reset", settings_reset),
        web.post("/weather/test", weather_test),
        web.get("/reminders", reminders_page), web.post(r"/reminders/{id:\d+}/cancel", reminder_cancel),
        web.get("/notes", notes_page), web.post(r"/notes/{id:\d+}/delete", note_delete),
        web.get("/healthz", health),
    ])
    return app


def render_settings(s: dict) -> str:
    out = ["<form method='post' action='/settings'>"]
    for title, keys in GROUPS:
        out.append(f"<section><h2>{title}</h2>")
        for k in keys:
            v, label = s[k], LABELS.get(k, k)
            if isinstance(DEFAULTS[k], bool):
                out.append(f"<label class='check'><input type='checkbox' name='{k}' {'checked' if v else ''}> {label}</label>")
                continue
            out.append(f"<label for='{k}'>{label}</label>")
            if k == "weather_county":
                opts = "".join(f"<option {'selected' if c == v else ''}>{c}</option>" for c in COUNTIES)
                out.append(f"<select id='{k}' name='{k}'>{opts}</select>")
            elif k == "note_categories":
                out.append(f"<textarea id='{k}' name='{k}'>{escape(str(v))}</textarea>")
            elif isinstance(DEFAULTS[k], int):
                out.append(f"<input type='number' id='{k}' name='{k}' value='{v}'>")
            else:
                out.append(f"<input type='text' id='{k}' name='{k}' value='{escape(str(v), quote=True)}'>")
            if k in HINTS:
                out.append(f"<div class='hint'>可用：{escape(HINTS[k])}</div>")
        out.append("</section>")
    out.append("<div class='bar'><button>儲存</button></div></form>")
    out.append("<section><h2>工具</h2><form method='post' action='/weather/test' style='display:inline'>"
               "<button class='ghost'>現在貼一次天氣到頻道</button></form> "
               "<form method='post' action='/settings/reset' style='display:inline' "
               "onsubmit=\"return confirm('全部設定恢復預設？')\"><button class='ghost'>全部恢復預設</button></form></section>")
    return "".join(out)


async def start_web(bot) -> web.AppRunner:
    runner = web.AppRunner(make_app(bot), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, bot.config.web_host, bot.config.web_port)
    await site.start()
    log.info("設定網頁：http://%s:%s", bot.config.web_host, bot.config.web_port)
    return runner
