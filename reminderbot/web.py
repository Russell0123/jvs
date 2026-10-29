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
from .explain import describe, rules
from .router import route
from .settings import (COUNTIES, DEFAULTS, GROUPS, LABELS, MULTILINE_KEYS, REPLY_GROUPS, REPLY_KEYS, SETTING_KEYS,
                       note_categories, validate, wish_lists)

log = logging.getLogger(__name__)

STATUS = {"pending": "等待中", "fired": "已響・未完成", "done": "已完成", "cancelled": "已取消", "expired": "已過期"}

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
.filters{display:flex;flex-wrap:wrap;gap:4px 12px;margin-bottom:10px}.filters a{color:var(--muted)}.rowform{display:flex;gap:6px;align-items:center;margin:0 0 6px}.rowform input[type=text]{flex:1;min-width:8em}.rowform select{width:auto}.filters a.on{color:var(--accent);font-weight:600}
"""


def page(title: str, active: str, body: str, flash: str = "", error: bool = False) -> web.Response:
    nav = "".join(f'<a href="{href}" class="{"on" if key == active else ""}">{name}</a>'
                  for key, href, name in [("reminders", "/reminders", "提醒"), ("notes", "/notes", "清單"),
                                          ("replies", "/replies", "回覆"), ("logic", "/logic", "判斷"),
                                          ("settings", "/", "設定")])
    fl = f'<div class="flash{" err" if error else ""}">{flash}</div>' if flash else ""
    html = (f"<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'><title>{escape(title)}</title>"
            f"<style>{CSS}</style></head><body><header><b>jvs</b><nav>{nav}</nav></header>"
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

    def form_page(kind: str, flash: str = "", error: bool = False):
        title, groups, path = ("回覆", REPLY_GROUPS, "/replies") if kind == "replies" else ("設定", GROUPS, "/")
        return page(title, kind, render_form(bot.store.settings(), groups, path, kind == "settings"), flash, error)

    async def settings_page(request: web.Request):
        msg = {"saved": "✅ 已儲存", "weather": "✅ 已貼到頻道"}.get(request.query.get("ok", ""), "")
        return form_page("settings", msg)

    async def replies_page(request: web.Request):
        return form_page("replies", "✅ 已儲存" if request.query.get("ok") else "")

    async def save(request: web.Request, kind: str):
        form = await request.post()
        keys = REPLY_KEYS if kind == "replies" else SETTING_KEYS
        values, errors = validate(dict(form), keys)
        bot.store.save_settings(values)
        if errors:
            return form_page(kind, "沒存到：" + escape("；".join(errors)), error=True)
        raise web.HTTPSeeOther(("/replies" if kind == "replies" else "/") + "?ok=saved")

    async def reset(request: web.Request, kind: str):
        keys = REPLY_KEYS if kind == "replies" else SETTING_KEYS
        bot.store.save_settings({k: DEFAULTS[k] for k in keys})
        raise web.HTTPSeeOther(("/replies" if kind == "replies" else "/") + "?ok=saved")

    async def save_settings(request):
        return await save(request, "settings")

    async def save_replies(request):
        return await save(request, "replies")

    async def reset_settings(request):
        return await reset(request, "settings")

    async def reset_replies(request):
        return await reset(request, "replies")

    async def weather_test(request: web.Request):
        try:
            await bot.post_weather_now()
        except Exception as e:
            return form_page("settings", "天氣發送失敗：" + escape(str(e)), error=True)
        raise web.HTTPSeeOther("/?ok=weather")

    async def reminders_page(request: web.Request):
        f = request.query.get("f", "active")
        statuses = {"active": ("pending", "fired"), "done": ("done", "cancelled", "expired"),
                    "all": ("pending", "fired", "done", "cancelled", "expired")}.get(f, ("pending", "fired"))
        rems = bot.store.all_reminders(statuses)
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
        bot.store.update_reminder(int(request.match_info["id"]), status="cancelled")
        raise web.HTTPSeeOther("/reminders")

    async def notes_page(request: web.Request):
        notes = bot.store.all_notes()
        who = await names([n["user_id"] for n in notes])
        s = bot.store.settings()
        cats = [n for n, _ in wish_lists(s)] + [n for n, _ in note_categories(s)]
        cats += sorted({n["category"] for n in notes} - set(cats))
        f = request.query.get("c", "")
        shown = [n for n in notes if not f or n["category"] == f]
        shown.sort(key=lambda n: (cats.index(n["category"]) if n["category"] in cats else 99,
                                  -(n.get("mentioned_at") or n["created_at"])))

        def opts(cur):
            return "".join(f"<option {'selected' if c == cur else ''}>{escape(c)}</option>" for c in cats)
        rows = "".join(
            f"<tr><td>#{n['id']}</td><td colspan=2><form class='rowform' method='post' action='/notes/{n['id']}/edit'>"
            f"<select name='category'>{opts(n['category'])}</select>"
            f"<input type='text' name='text' value='{escape(n['text'], quote=True)}'>"
            f"<button class='small'>儲存</button></form></td>"
            f"<td>{escape(who.get(n['user_id'], ''))}</td>"
            f"<td>{datetime.fromtimestamp(n.get('mentioned_at') or n['created_at'], bot.tz):%m/%d}"
            f"{'・提過 ' + str(n['mentions']) + ' 次' if (n.get('mentions') or 1) > 1 else ''}</td>"
            f"<td><form method='post' action='/notes/{n['id']}/delete'><button class='small danger'>刪除</button></form></td></tr>"
            for n in shown)
        filt = f'<a href="?" class="{"" if f else "on"}">全部</a>' + "".join(
            f'<a href="?c={escape(c)}" class="{"on" if c == f else ""}">{escape(c)}</a>' for c in cats)
        owners = sorted({n["user_id"] for n in notes} | ({bot.config.owner_id} if bot.config.owner_id else set()))
        add = (f"<form class='rowform' method='post' action='/notes/add'>"
               f"<select name='user_id'>{''.join(f'<option value={u}>{escape(who.get(u, str(u)))}</option>' for u in owners)}</select>"
               f"<select name='category'>{opts(f or (cats[0] if cats else ''))}</select>"
               f"<input type='text' name='text' placeholder='新增一項，例如：穿西裝的柴犬' required>"
               f"<button class='small'>新增</button></form>") if owners else ""
        body = (f"<section><h2>清單</h2>"
                f"<div class='filters'>{filt}</div>{add}<div class='wrap'><table><tr><th>#</th><th colspan=2>清單／內容</th>"
                f"<th>誰的</th><th>最後提到</th><th></th></tr>{rows or '<tr><td colspan=6>沒有資料</td></tr>'}"
                f"</table></div></section>")
        return page("清單", "notes", body)

    async def note_edit(request: web.Request):
        form = await request.post()
        text = str(form.get("text", "")).strip()
        if text:
            bot.store.update_note(int(request.match_info["id"]), text=text,
                                  category=str(form.get("category", "")).strip() or "備忘")
        raise web.HTTPSeeOther(request.headers.get("Referer") or "/notes")

    async def note_add(request: web.Request):
        form = await request.post()
        text = str(form.get("text", "")).strip()
        if text and str(form.get("user_id", "")).isdigit():
            bot.store.add_note(int(form["user_id"]), str(form.get("category", "備忘")), text,
                               datetime.now(bot.tz).timestamp())
        raise web.HTTPSeeOther(request.headers.get("Referer") or "/notes")

    async def note_delete(request: web.Request):
        bot.store.delete_note(int(request.match_info["id"]))
        raise web.HTTPSeeOther(request.headers.get("Referer") or "/notes")

    async def logic_page(request: web.Request):
        s = bot.store.settings()
        q = request.query.get("q", "").strip()
        result = ""
        if q:
            d = describe(route(q, datetime.now(bot.tz), s), bot.tz)
            result = (f"<div class='flash'><b>判斷：{escape(d['label'])}</b><br>{escape(d['detail']) or '（沒有細節）'}</div>")
        out = [f"<section><h2>試一句</h2><form class='rowform' method='get'><input type='text' name='q' "
               f"value='{escape(q, quote=True)}' placeholder='例如：每兩小時提醒我喝水'><button class='small'>看判斷</button>"
               f"</form>{result}</section>"]
        for sec in rules(s):
            rows = "".join(f"<tr><td><b>{escape(n)}</b></td><td>{escape(d)}</td><td>{escape('、'.join(w))}</td></tr>"
                           for n, d, w in sec["steps"])
            out.append(f"<section><h2>{escape(sec['title'])}</h2><div class='wrap'><table>"
                       f"<tr><th>規則</th><th>說明</th><th>關鍵字</th></tr>{rows}</table></div></section>")
        return page("判斷", "logic", "".join(out))

    async def health(request: web.Request):
        return web.json_response({"ok": True, "discord_ready": bot.is_ready()})

    app = web.Application(middlewares=[guard])
    app.add_routes([
        web.get("/", settings_page), web.post("/", save_settings), web.post("/reset", reset_settings),
        web.get("/replies", replies_page), web.post("/replies", save_replies), web.post("/replies/reset", reset_replies),
        web.post("/weather/test", weather_test),
        web.get("/reminders", reminders_page), web.post(r"/reminders/{id:\d+}/cancel", reminder_cancel),
        web.get("/notes", notes_page), web.post(r"/notes/{id:\d+}/delete", note_delete),
        web.post(r"/notes/{id:\d+}/edit", note_edit), web.post("/notes/add", note_add),
        web.get("/logic", logic_page), web.get("/healthz", health),
    ])
    return app


def render_form(s: dict, groups, path: str, tools: bool) -> str:
    out = [f"<form method='post' action='{path}'>"]
    for title, keys in groups:
        out.append(f"<section><h2>{title}</h2>")
        for k in keys:
            v, label = s[k], escape(LABELS.get(k, k))
            if isinstance(DEFAULTS[k], bool):
                out.append(f"<label class='check'><input type='checkbox' name='{k}' {'checked' if v else ''}> {label}</label>")
                continue
            out.append(f"<label for='{k}'>{label}</label>")
            if k == "weather_county":
                opts = "".join(f"<option {'selected' if c == v else ''}>{c}</option>" for c in COUNTIES)
                out.append(f"<select id='{k}' name='{k}'>{opts}</select>")
            elif k in MULTILINE_KEYS:
                rows = min(8, max(2, str(v).count("\n") + 1))
                out.append(f"<textarea id='{k}' name='{k}' rows='{rows}'>{escape(str(v))}</textarea>")
            elif isinstance(DEFAULTS[k], int):
                out.append(f"<input type='number' id='{k}' name='{k}' value='{v}'>")
            else:
                out.append(f"<input type='text' id='{k}' name='{k}' value='{escape(str(v), quote=True)}'>")
        out.append("</section>")
    out.append("<div class='bar'><button>儲存</button></div></form>")
    reset = "/reset" if path == "/" else f"{path}/reset"
    out.append("<section>" + ("<form method='post' action='/weather/test' style='display:inline'>"
                              "<button class='ghost'>貼一次天氣</button></form> " if tools else "") +
               f"<form method='post' action='{reset}' style='display:inline' "
               "onsubmit=\"return confirm('恢復預設？')\"><button class='ghost'>恢復預設</button></form></section>")
    return "".join(out)


async def start_web(bot) -> web.AppRunner:
    runner = web.AppRunner(make_app(bot), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, bot.config.web_host, bot.config.web_port)
    await site.start()
    log.info("設定網頁：http://%s:%s", bot.config.web_host, bot.config.web_port)
    return runner
