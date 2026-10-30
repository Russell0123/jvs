"""設定網頁：只綁 127.0.0.1，外面透過 Cloudflare Tunnel + Access（或 SSH 轉發）才連得到。"""
from __future__ import annotations

import base64
import contextvars
import hmac
import logging
import re
from datetime import datetime
from html import escape
from urllib.parse import urlencode, urlparse

from aiohttp import web

from . import food, images, rules, timeparse, updater
from .explain import describe, rules as logic_rules
from .core import IMG_TOKEN, NO_IMAGE
from .router import route
from .settings import (COUNTIES, DEFAULTS, FOOD_GROUPS, FOOD_KEYS, GROUPS, LABELS, MULTILINE_KEYS, REACTION_GROUPS,
                       REACTION_KEYS, REPLY_GROUPS, REPLY_KEYS, SETTING_KEYS, note_categories, validate, wish_lists)

log = logging.getLogger(__name__)

STATUS = {"pending": "等待中", "fired": "已響・未完成", "done": "已完成", "cancelled": "已取消", "expired": "已過期"}

CSS = """
:root{--rail:#1e1f22;--side:#2b2d31;--bg:#313338;--card:#2b2d31;--input:#1e1f22;--fg:#dbdee1;--head:#f2f3f5;
--muted:#949ba4;--line:#3f4147;--accent:#5865f2;--accent-h:#4752c4;--danger:#da373c;--ok:#23a559;--hover:#35373c;
--sel:#404249;--link:#00a8fc}
*{box-sizing:border-box}html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 "gg sans","Noto Sans TC","Microsoft JhengHei","PingFang TC",system-ui,sans-serif}
a{color:var(--link)}
.app{display:flex;height:100vh}
.rail{width:72px;flex:none;background:var(--rail);display:flex;flex-direction:column;align-items:center;gap:8px;padding:12px 0;overflow-y:auto}
.rail a{position:relative;width:48px;height:48px;border-radius:50%;background:var(--bg);color:var(--head);display:flex;
align-items:center;justify-content:center;font-weight:700;font-size:18px;text-decoration:none;transition:border-radius .15s,background .15s}
.rail a:hover,.rail a.on{border-radius:16px;background:var(--accent);color:#fff}
.rail a.on::before{content:"";position:absolute;left:-12px;top:4px;width:4px;height:40px;border-radius:0 4px 4px 0;background:var(--head)}
.rail .sep{width:32px;height:2px;background:var(--line);border-radius:1px}
.side{width:240px;flex:none;background:var(--side);display:flex;flex-direction:column;overflow-y:auto}
.side-head{height:48px;flex:none;padding:0 16px;display:flex;align-items:center;font-weight:700;color:var(--head);box-shadow:0 1px 0 rgba(0,0,0,.25)}
.side nav{padding:8px}
.cat{margin:16px 8px 4px;font-size:12px;font-weight:700;color:var(--muted);letter-spacing:.02em}
.side nav a{display:flex;gap:6px;align-items:center;padding:6px 8px;margin:1px 0;border-radius:4px;color:var(--muted);text-decoration:none;font-weight:500}
.side nav a::before{content:"#";font-size:20px;line-height:1;color:#80848e;width:20px;text-align:center}
.side nav a:hover{background:var(--hover);color:var(--fg)}.side nav a.on{background:var(--sel);color:var(--head)}
.col{flex:1;min-width:0;display:flex;flex-direction:column}
.top{height:48px;flex:none;display:flex;align-items:center;gap:8px;padding:0 16px;box-shadow:0 1px 0 rgba(0,0,0,.25);z-index:1}
.top .hash{color:#80848e;font-size:24px;font-weight:300}.top b{color:var(--head);font-size:16px}.top .sp{flex:1}
.upd{display:flex;gap:8px;align-items:center;margin:0}.upd a{font-size:12px;color:var(--muted);text-decoration:none}
.upd button{padding:4px 12px;font-size:13px}.upd button.new{background:var(--ok)}
main{flex:1;overflow-y:auto;padding:16px 16px 0}.inner{max-width:880px}
section{background:var(--card);border-radius:8px;padding:12px 16px;margin-bottom:8px}
h2{font-size:15px;margin:0;color:var(--head);font-weight:600}
section.fold>h2{cursor:pointer;user-select:none;display:flex;align-items:center;gap:8px;margin:-12px -16px;padding:12px 16px}
section.fold>h2::before{content:"›";color:var(--muted);font-size:20px;line-height:1;transition:transform .15s;width:10px}
section.fold.open>h2::before{transform:rotate(90deg)}section.fold.open>h2{margin-bottom:4px}
section.fold:not(.open)>:not(h2){display:none}section.fold>h2:hover{color:#fff}
section:not(.fold)>h2{margin-bottom:12px}
label{display:block;margin:12px 0 6px;color:var(--muted);font-size:12px;font-weight:700}
input[type=text],input[type=number],select,textarea{width:100%;padding:9px 10px;border:0;border-radius:4px;background:var(--input);color:var(--fg);font:inherit}
input:focus,select:focus,textarea:focus{outline:2px solid var(--accent);outline-offset:0}
textarea{min-height:96px;resize:vertical}.check{display:flex;gap:8px;align-items:center;margin:10px 0;color:var(--fg);font-size:15px;font-weight:400}
input[type=checkbox]{accent-color:var(--accent);width:18px;height:18px}
.hint{color:var(--muted);font-size:12px;margin-top:2px}
button{background:var(--accent);color:#fff;border:0;border-radius:4px;padding:8px 16px;font:inherit;font-weight:500;cursor:pointer}
button:hover{background:var(--accent-h)}button:disabled{opacity:.5;cursor:not-allowed}
button.small{padding:3px 10px;font-size:13px}button.danger{background:var(--danger)}
button.ghost{background:#4e5058;color:#fff}button.ghost:hover{background:#6d6f78}
.btnrow{margin-top:12px}.bar{position:sticky;bottom:0;background:var(--bg);padding:12px 0;display:flex;gap:10px;z-index:1}.bar .sp{flex:1}
.flash{padding:10px 14px;border-radius:8px;margin-bottom:12px;background:rgba(88,101,242,.18);border-left:4px solid var(--accent)}
.flash.err{background:rgba(218,55,60,.15);border-left-color:var(--danger)}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:8px 6px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:12px;white-space:nowrap}.wrap table{min-width:600px}td:first-child,td:nth-child(2){white-space:nowrap}
.tag{font-size:12px;color:var(--muted)}.wrap{overflow-x:auto}p.tag{margin:0 0 10px}
.filters{display:flex;flex-wrap:wrap;gap:4px 12px;margin-bottom:10px}.filters a{color:var(--muted);text-decoration:none}.filters a.on{color:var(--head);font-weight:600}
.rowform{display:flex;gap:6px;align-items:center;margin:0 0 6px}.rowform input[type=text]{flex:1;min-width:8em}.rowform select{width:auto}
code{background:var(--input);border-radius:4px;padding:1px 5px;font-size:13px}
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(110px,1fr));gap:8px;margin-top:8px}.gallery figure{margin:0}
.gallery img{width:100%;aspect-ratio:1;object-fit:cover;border-radius:8px;display:block;margin-bottom:4px}
.gallery .code{display:block;background:none;border:0;padding:0 0 4px;color:var(--muted);font:12px ui-monospace,monospace;cursor:pointer}.gallery .code[data-ok]{color:var(--link)}
.gallery .lost{aspect-ratio:1;border:1px dashed var(--line);border-radius:8px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px;font-size:12px;color:var(--muted);margin-bottom:4px}
.gallery .lost label{margin:0;cursor:pointer;color:#fff;background:#4e5058;border-radius:4px;padding:3px 8px}.gallery .lost input{display:none}
.rulelist{list-style:none;margin:8px 0 0;padding:0}
.rulelist li{display:flex;gap:8px;align-items:flex-start;border-radius:6px;padding:8px 10px;margin-bottom:4px;background:var(--bg)}
.rulelist li:hover{background:var(--hover)}.rulelist li.off summary b,.rulelist li.off .kw{opacity:.45}
.rulelist li.dragging{opacity:.7;outline:2px solid var(--accent)}
.rulelist .handle{cursor:grab;touch-action:none;user-select:none;color:var(--muted);padding:2px;line-height:1.4}
.rulelist .rtoggle{margin-top:3px;flex:none}.rulelist details{flex:1;min-width:0}.rulelist summary{cursor:pointer;line-height:1.5;color:var(--head)}
.rulelist summary .kw{color:var(--link);margin-left:6px;font-size:14px;overflow-wrap:anywhere}.rulelist summary .tag{display:block}
.newrule summary{cursor:pointer;font-weight:600;color:var(--head)}.toast{font-size:13px;color:var(--ok);margin-left:10px;font-weight:400}
.badge{background:var(--ok);color:#fff;border-radius:4px;padding:1px 8px;font-size:12px;font-weight:600}
pre.log{white-space:pre-wrap;font-size:12px;background:var(--input);border-radius:6px;padding:8px;max-height:320px;overflow:auto}
details>summary{list-style:none}details>summary::-webkit-details-marker{display:none}
.rulelist details>summary::before,.newrule summary::before{content:"› ";color:var(--muted)}
.rulelist details[open]>summary::before,.newrule details[open] summary::before{content:"⌄ "}
@media (max-width:760px){
.app{flex-direction:column;height:auto;min-height:100%}
.rail{width:auto;flex-direction:row;padding:8px 12px;overflow-x:auto}.rail a{width:40px;height:40px;font-size:16px}
.rail a.on::before{display:none}.rail .sep{width:2px;height:28px}
.side{width:auto;overflow:visible}.side-head{display:none}
.side nav{display:flex;gap:2px;overflow-x:auto;white-space:nowrap;padding:6px 8px;align-items:center}
.cat{margin:0 4px 0 10px;font-size:11px}.side nav a{padding:4px 8px}.side nav a::before{font-size:16px;width:12px}
.top{position:sticky;top:0;background:var(--bg)}main{overflow:visible}}
"""


NAV_ITEMS = [("記憶", [("reminders", "/reminders", "提醒"), ("notes", "/notes", "清單")]),
             ("回覆", [("replies", "/replies", "預設回覆"), ("rules", "/rules", "自訂回覆"), ("food", "/food", "吃什麼"),
                     ("reactions", "/reactions", "表情符號")]),
             ("設定", [("settings", "/", "設定"), ("logic", "/logic", "判斷")])]

PAGE_JS = """<script>
(() => {
  const B = document.body.dataset.base || "";
  // 每個區塊預設摺起來，點標題展開；這次瀏覽中打開過的會記住
  const hash = location.hash ? document.querySelector(location.hash) : null;
  const secs = [...document.querySelectorAll("main section")].filter(x => x.firstElementChild && x.firstElementChild.tagName === "H2");
  for (const sec of secs) {
    const h = sec.firstElementChild;
    sec.classList.add("fold");
    const key = "fold:" + location.pathname + ":" + h.textContent.replace(/[（(].*$/, "");
    let open = false;
    try { open = sessionStorage.getItem(key) === "1"; } catch (e) {}
    if (hash && (sec === hash || sec.contains(hash))) open = true;
    if (secs.length === 1) open = true;           // 只有一塊就直接打開
    sec.classList.toggle("open", open);
    h.addEventListener("click", e => {
      if (e.target.closest("button,a,input")) return;
      const now = sec.classList.toggle("open");
      try { sessionStorage.setItem(key, now ? "1" : "0"); } catch (e) {}
    });
  }
  if (hash) hash.scrollIntoView();
  // 右上角一鍵更新
  const btn = document.getElementById("updbtn"), info = document.getElementById("updinfo");
  if (!btn) return;
  const load = () => fetch(B + "/update.json").then(r => r.json()).then(d => {
    btn.classList.toggle("new", !!d.newer);
    if (d.running) { btn.disabled = true; btn.textContent = "⏳ 更新中…"; setTimeout(load, 10000); }
    else { btn.disabled = false; btn.textContent = d.newer ? "⬆ 有新版本，一鍵更新" : "一鍵更新"; }
    info.textContent = d.current ? (d.newer && d.latest ? d.current + " → " + d.latest : d.current) : "";
    info.title = d.last || "";
  }).catch(() => {});
  load();
})();
</script>"""


def page(title: str, active: str, body: str, flash: str = "", error: bool = False) -> web.Response:
    nav = NAV.get()
    prefix = nav["prefix"]
    rail = "".join(f"<a href='{pre or ''}/' class='{'on' if key == nav['key'] else ''}' title='{escape(name)}'>"
                   f"{escape(name[:1])}</a>" for key, name, pre in nav["bots"]) or "<a class='on'>賈</a>"
    side = "".join(f"<div class='cat'>{cat}</div>" + "".join(
        f"<a href='{href}' class='{'on' if key == active else ''}'>{name}</a>" for key, href, name in items)
        for cat, items in NAV_ITEMS)
    fl = f'<div class="flash{" err" if error else ""}">{flash}</div>' if flash else ""
    upd = ("<form class='upd' method='post' action='/update' onsubmit=\"return confirm('更新到 GitHub 上最新的版本？"
           "會重新啟動，大約 1 分鐘；新版起不來會自動退回。')\"><a id='updinfo' href='/update'></a>"
           "<button id='updbtn'>一鍵更新</button></form>")
    html = (f"<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{escape(title)}・{escape(nav['name'] or 'jvs')}</title><style>{CSS}</style></head>"
            f"<body data-base='{prefix}'><div class='app'><nav class='rail'>{rail}</nav>"
            f"<aside class='side'><div class='side-head'>{escape(nav['name'] or '賈維斯')}</div><nav>{side}</nav></aside>"
            f"<div class='col'><header class='top'><span class='hash'>#</span><b>{escape(title)}</b>"
            f"<span class='sp'></span>{upd}</header>"
            f"<main><div class='inner'>{fl}{body}</div></main></div></div>{PAGE_JS}</body></html>")
    if prefix:      # 在別的 bot 底下：站內連結都加上 /b/<代號>
        html = re.sub(r"""((?:href|action|src)=['"])/(?!/|b/)""", rf"\1{prefix}/", html)
    return web.Response(text=html, content_type="text/html")


# 同一個網頁管好幾個 bot：網址 /b/<代號>/... 是那個 bot，沒有前綴的是第一個（賈維斯）。
# 每個請求開始時把「現在是哪個 bot」放進 contextvar，下面的程式照舊用 bot.xxx 就會拿到對的那個。
CURRENT: contextvars.ContextVar = contextvars.ContextVar("bot")
NAV: contextvars.ContextVar = contextvars.ContextVar("nav", default={"prefix": "", "bots": [], "key": "", "name": ""})


class _CurrentBot:
    def __getattr__(self, name):
        return getattr(CURRENT.get(), name)


def make_app(bots) -> web.Application:
    bots = list(bots) if isinstance(bots, (list, tuple)) else [bots]
    cfg = bots[0].config                      # 帳密、更新：大家共用
    prefixes = {b.config.key: ("" if i == 0 else f"/b/{b.config.key}") for i, b in enumerate(bots)}
    by_key = {b.config.key: b for b in bots}
    rail = [(b.config.key, getattr(b.config, "name", b.config.key), prefixes[b.config.key]) for b in bots]
    bot = _CurrentBot()

    @web.middleware
    async def guard(request: web.Request, handler):
        m = re.match(r"^/b/([a-z0-9_]+)(?=/|$)", request.path)
        current = by_key.get(m.group(1)) if m else bots[0]
        if current is None:
            raise web.HTTPNotFound()
        prefix = prefixes[current.config.key]
        CURRENT.set(current)
        NAV.set({"prefix": prefix, "bots": rail, "key": current.config.key,
                 "name": getattr(current.config, "name", current.config.key)})
        try:
            return await guarded(request, handler)
        except web.HTTPRedirection as e:
            # 程式裡寫的是 /rules 這種網址，在別的 bot 底下要加上 /b/<代號>
            loc = str(e.location)
            if prefix and loc.startswith("/") and not loc.startswith(prefix + "/"):
                raise type(e)(prefix + loc)
            raise

    async def guarded(request: web.Request, handler):
        if request.path == "/healthz":       # 更新程式時確認服務活著用，不用帳密（只回 bot 有沒有連上）
            return await handler(request)
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
                    ok = hmac.compare_digest(user, cfg.web_user) and hmac.compare_digest(pw, cfg.web_password)
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
            if u is None and not bot.store.get_meta(f"uname:{i}"):
                try:
                    u = await bot.fetch_user(i)
                except Exception:
                    u = None
            out[i] = u.display_name if u else bot.store.get_meta(f"uname:{i}") or str(i)
        return out

    def form_page(kind: str, flash: str = "", error: bool = False, extra: str = ""):
        title, groups, path = PAGES[kind]
        s = bot.store.settings()
        intro = {"replies": REPLY_HELP, "food": food_summary(s), "reactions": REACTION_HELP}.get(kind, "")
        return page(title, kind, intro + render_form(s, groups, path, kind == "settings", tall=kind == "food") + extra,
                    flash, error)

    def food_summary(s: dict) -> str:
        foods, ferr = food.parse_library(s["food_library"])
        tree, qerr = food.parse_questions(s["food_questions"])
        names = {o.code: o.label for o in tree.first.options} if tree.first else {}
        routes = "、".join(f"{names.get(k, k)} {sum(1 for f in foods if k in f.tags.get(food.ROUTE, set()))} 項"
                          for k in tree.branches)
        errs = "".join(f"<br>⚠️ {escape(e)}" for e in (qerr + ferr)[:15])
        return (f"<p class='tag'>叫賈維斯說「我要吃什麼」就會一題一題問，最後給前 5 名。目前讀到 {len(foods)} 項食物"
                f"{'：' + escape(routes) if routes else ''}。{errs}</p>")

    def missing_codes(values: dict) -> list[str]:
        codes = bot.core.image_codes()
        found = {c for v in values.values() if isinstance(v, str) for c in IMG_TOKEN.findall(v)}
        return sorted(c for c in found if c not in codes and c not in NO_IMAGE)

    async def settings_page(request: web.Request):
        msg = {"saved": "✅ 已儲存", "weather": "✅ 已貼到頻道"}.get(request.query.get("ok", ""), "")
        return form_page("settings", msg)

    async def replies_page(request: web.Request):
        q = request.query
        flash = escape(q.get("err", "")) or (f"✅ 上傳了 {escape(q['ok'])} 張" if q.get("ok", "").isdigit()
                                             else "✅ 已儲存" if q.get("ok") else "")
        imgs, warn = images_sections()
        return form_page("replies", flash or warn, bool(q.get("err")), extra=imgs)

    async def reactions_page(request: web.Request):
        return form_page("reactions", "✅ 已儲存" if request.query.get("ok") else "")

    async def save_reactions(request):
        return await save(request, "reactions")

    async def reset_reactions(request):
        return await reset(request, "reactions")

    async def save(request: web.Request, kind: str):
        form = await request.post()
        keys = KEYS[kind]
        values, errors = validate(dict(form), keys)
        bot.store.save_settings(values)
        if errors:
            return form_page(kind, "沒存到：" + escape("；".join(errors)), error=True)
        if missing := missing_codes(values):
            return form_page(kind, "已儲存，但圖片頁找不到這些代碼：" + escape("、".join(f"{{圖:{c}}}" for c in missing)),
                             error=True)
        raise web.HTTPSeeOther(PAGES[kind][2] + "?ok=saved")

    async def reset(request: web.Request, kind: str):
        bot.store.save_settings({k: DEFAULTS[k] for k in KEYS[kind]})
        raise web.HTTPSeeOther(PAGES[kind][2] + "?ok=saved")

    async def food_page(request: web.Request):
        return form_page("food", "✅ 已儲存" if request.query.get("ok") else "")

    async def save_food(request):
        return await save(request, "food")

    async def reset_food(request):
        return await reset(request, "food")

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
        flash = {"ok": "✅ 改好了"}.get(request.query.get("m", ""), escape(request.query.get("e", "")))
        for r in rems:
            when = "下雨天" if r["kind"] == "rain" else timeparse.fmt_dt(datetime.fromtimestamp(r["due_at"], bot.tz), bot.tz)
            targets = "、".join(who.get(int(t[2:]), t) if t.startswith("u:") else f"身分組 {t[2:]}"
                               for t in r["targets"])
            rep = timeparse.describe_repeat(r["repeat"])
            kind = "⏱️ 計時" if r["kind"] == "timer" else ("🔁 " + rep if rep else "提醒")
            active = r["status"] in ("pending", "fired")
            if active:
                cell_when = (f"<input form='e{r['id']}' type='text' name='when' placeholder='{escape(when, quote=True)}'"
                             f" title='例如：明天下午3點、10分鐘後、每天早上8點'>")
                cell_text = f"<input form='e{r['id']}' type='text' name='text' value='{escape(r['text'], quote=True)}'>"
                action = (f"<form id='e{r['id']}' method='post' action='/reminders/{r['id']}/edit' style='display:inline'>"
                          f"<button class='small'>存</button></form> "
                          f"<form method='post' action='/reminders/{r['id']}/cancel' style='display:inline'>"
                          f"<button class='small danger'>取消</button></form>")
            else:
                cell_when, cell_text, action = when, escape(r["text"]), ""
            rows.append(f"<tr><td>#{r['id']}</td><td>{cell_when}</td><td>{cell_text}"
                        f"<div class='tag'>{kind}</div></td><td>{escape(who.get(r['creator_id'], ''))}</td>"
                        f"<td>{escape(targets)}</td><td>{STATUS.get(r['status'], r['status'])}</td>"
                        f"<td style='white-space:nowrap'>{action}</td></tr>")
        filt = "".join(f'<a href="?f={k}" class="{"on" if k == f else ""}">{n}</a>'
                       for k, n in [("active", "進行中"), ("done", "已結束"), ("all", "全部")])
        body = (f"<section><h2>提醒</h2><div class='filters'>{filt}</div><div class='wrap'><table>"
                f"<tr><th>#</th><th>時間</th><th>內容</th><th>建立者</th><th>提醒誰</th><th>狀態</th><th></th></tr>"
                f"{''.join(rows) or '<tr><td colspan=7>沒有資料</td></tr>'}</table></div></section>")
        return page("提醒", "reminders", body, flash, error=bool(request.query.get("e")))

    async def reminder_edit(request: web.Request):
        form = await request.post()
        ok, msg = bot.core.edit_reminder(int(request.match_info["id"]), None, datetime.now(bot.tz),
                                         when_text=str(form.get("when", "")), new_text=str(form.get("text", "")))
        from urllib.parse import quote
        raise web.HTTPSeeOther("/reminders?m=ok" if ok else f"/reminders?e={quote(msg)}")

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
        for sec in logic_rules(s):
            rows = "".join(f"<tr><td><b>{escape(n)}</b></td><td>{escape(d)}</td><td>{escape('、'.join(w))}</td></tr>"
                           for n, d, w in sec["steps"])
            out.append(f"<section><h2>{escape(sec['title'])}</h2><div class='wrap'><table>"
                       f"<tr><th>規則</th><th>說明</th><th>關鍵字</th></tr>{rows}</table></div></section>")
        return page("判斷", "logic", "".join(out))

    def img_root():
        return images.folder(bot.config.db_path)

    def images_sections() -> tuple[str, str]:
        """預設回覆頁下面的圖庫（打招呼、稀有招呼、Stark says）。回 (html, 檔案不見的提醒)。"""
        s = bot.store.settings()
        out = [f"<p class='tag' id='images'>圖片：打招呼時從對應的分組隨機挑一張附上，附圖機率 "
               f"{escape(str(s['greet_image_percent']))}%，在「設定 → 叫它」改。稀有招呼沒放圖就不附。"
               "每張圖下面的代碼（例如 {圖:3}）按一下就複製，貼到回覆句子後面，那句就一定配那張圖。</p>"]
        lost = sum(1 for g in images.GROUPS for n in bot.core.images(g) if not images.path_of(img_root(), f"{g}/{n}"))
        warn = (f"⚠️ 有 {lost} 張圖的檔案不見了。在那張的位置按「重新上傳」選一張圖就好，"
                "代碼不變，回覆裡的 {圖:代碼} 不用改。") if lost else ""
        for g, gname in images.GROUPS.items():
            names = bot.core.images(g)
            empty = ("<p class='tag'>沒放圖時用這張預設的：</p><div class='gallery'><figure>"
                     "<img src='/images/builtin/stark.png' alt=''></figure></div>") if g == "stark" else ""
            out.append(f"<section id='img-{g}'><h2>🖼️ 圖片：{gname}（{len(names)} 張）</h2>{gallery(g, names, '')}"
                       f"{'' if names else empty}</section>")
        return "".join(out), warn

    async def images_page(request: web.Request):
        raise web.HTTPSeeOther("/replies?" + request.query_string + "#images")

    def gallery(group: str, names: list[str], back: str) -> str:
        q = f"?back={back}" if back else ""
        def tile(n: str) -> str:
            en = escape(n, quote=True)
            if images.path_of(img_root(), f"{group}/{n}"):
                pic = f"<img src='/images/{group}/{en}' loading='lazy' alt=''>"
            else:       # 檔案不見了：原位置重新上傳，代碼不變
                pic = (f"<form class='lost' method='post' action='/images/{group}/{en}/replace{q}' "
                       f"enctype='multipart/form-data'><span>⚠️ 檔案不見了</span>"
                       f"<label class='ghost small'>重新上傳<input type='file' name='files' "
                       f"accept='image/png,image/jpeg,image/gif,image/webp' required onchange='this.form.submit()'>"
                       f"</label></form>")
            return (f"<figure>{pic}<button type='button' class='code' title='複製代碼' "
                    f"onclick=\"navigator.clipboard.writeText(this.textContent);this.dataset.ok=1\">"
                    f"{{圖:{bot.core.image_code(f'{group}/{n}')}}}</button>"
                    f"<form method='post' action='/images/{group}/{en}/delete{q}' "
                    f"onsubmit=\"return confirm('刪掉這張？')\"><button class='ghost small'>刪除</button></form></figure>")
        tiles = "".join(tile(n) for n in names)
        return (f"<form class='rowform' method='post' action='/images/{group}/upload{q}' enctype='multipart/form-data'>"
                f"<input type='file' name='files' accept='image/png,image/jpeg,image/gif,image/webp' multiple required>"
                f"<button class='small'>上傳</button></form>" + (f"<div class='gallery'>{tiles}</div>" if tiles else ""))

    def back_to(request: web.Request, group: str, query: str) -> str:
        if request.query.get("back") == "rules":
            return f"/rules?{query}#{group}"
        return f"/replies?{query}#img-{group}"

    async def image_file(request: web.Request):
        p = images.path_of(img_root(), f"{request.match_info['group']}/{request.match_info['name']}")
        if not p:
            raise web.HTTPNotFound()
        return web.FileResponse(p, headers={"Content-Type": images.TYPES.get(p.suffix, "application/octet-stream")})

    async def image_upload(request: web.Request):
        group = request.match_info["group"]
        if not images.valid_group(group):
            raise web.HTTPNotFound()
        added, errors = [], []
        reader = await request.multipart()
        while (part := await reader.next()) is not None:
            if part.name != "files" or not part.filename:
                continue
            data = await part.read(decode=False)
            try:
                added.append(images.save(img_root(), group, bytes(data)))
            except ValueError as e:
                errors.append(f"{part.filename}：{e}")
        if added:
            bot.core.set_images(group, bot.core.images(group) + added)
        if errors:
            raise web.HTTPSeeOther(back_to(request, group, urlencode({"err": "沒存到 " + "；".join(errors)})))
        raise web.HTTPSeeOther(back_to(request, group, f"ok={len(added)}"))

    async def image_replace(request: web.Request):
        group, name = request.match_info["group"], request.match_info["name"]
        if not images.valid_group(group) or name not in bot.core.images(group):
            raise web.HTTPNotFound()
        reader = await request.multipart()
        part = await reader.next()
        try:
            data = bytes(await part.read(decode=False)) if part is not None else b""
            new = images.save(img_root(), group, data, stem=name.rsplit(".", 1)[0])
        except ValueError as e:
            raise web.HTTPSeeOther(back_to(request, group, urlencode({"err": f"沒存到：{e}"})))
        if new != name:
            images.delete(img_root(), group, name)
            bot.core.replace_image(group, name, new)
        raise web.HTTPSeeOther(back_to(request, group, "ok=1"))

    async def image_delete(request: web.Request):
        group, name = request.match_info["group"], request.match_info["name"]
        if not images.valid_group(group):
            raise web.HTTPNotFound()
        images.delete(img_root(), group, name)
        bot.core.set_images(group, [n for n in bot.core.images(group) if n != name])
        raise web.HTTPSeeOther(back_to(request, group, ""))

    # ---------- 自訂規則 ----------
    def rule_fields(r: dict, who_names: str) -> str:
        def sel(key, choices):
            return f"<select name='{key}'>" + "".join(
                f"<option value='{k}' {'selected' if r[key] == k else ''}>{v}</option>" for k, v in choices.items()) \
                + "</select>"
        val = lambda k: escape(str(r[k]), quote=True)  # noqa: E731
        users = ", ".join(str(u) for u in r["users"])
        return (
            f"<label>名稱</label><input type='text' name='name' value='{val('name')}' placeholder='例如：吐槽報告'>"
            f"<label class='check'><input type='checkbox' name='on' {'checked' if r['on'] else ''}> 啟用</label>"
            f"<label>誰說的</label>{sel('who', rules.WHO)}"
            f"<label>指定的人：Discord 使用者 ID，逗號分開{who_names}</label>"
            f"<input type='text' name='users' value='{users}' placeholder='只有選「指定的人」才用到'>"
            f"<label>要不要叫它</label>{sel('call', rules.CALL)}"
            f"<label>有沒有交代事情</label>{sel('task', rules.TASK)}"
            f"<label>包含以下任一個詞，用 / 分開，不填＝不限</label>"
            f"<input type='text' name='words' value='{val('words')}' placeholder='報告/作業'>"
            f"<label>但不包含</label><input type='text' name='not_words' value='{val('not_words')}' placeholder='交了'>"
            f"<label>符合的話</label>{sel('mode', rules.MODE)}"
            f"<label>回覆：一行一句隨機挑，多行的句子用 \" \" 包起來，句子後面加 {{圖:代碼}} 配指定的圖、{{圖:無}} 不附圖；"
            f"可用 {{時段}} {{名字}} {{天氣}}，可以不填只回圖</label>"
            f"<textarea name='replies' rows='3'>{escape(r['replies'])}</textarea>"
            f"<label>冷卻：同一個頻道幾分鐘內只回一次，0＝不限</label>"
            f"<input type='number' name='cooldown' min='0' value='{r['cooldown']}'>")

    async def rules_page(request: web.Request, flash: str = "", error: bool = False, draft: dict | None = None):
        all_rules = rules.load(bot.store)
        if draft:
            all_rules = [draft if r["id"] == draft["id"] else r for r in all_rules]
        who = await names([u for r in all_rules for u in r["users"]])
        flash = flash or escape(request.query.get("err", "")) or ("✅ 已儲存" if "ok" in request.query else "")
        out = ["<p class='tag'>每則訊息由上往下比對，第一條符合的規則生效；按住左邊的 ⋮⋮ 拖曳調整優先順序，"
               "勾選框是啟用／停用。點標題展開編輯。</p>",
               f"<section class='newrule' id='new'><details {'open' if not all_rules else ''}><summary>＋ 新增規則</summary>"
               f"<form method='post' action='/rules/new'>{rule_fields(dict(rules.BLANK), '')}"
               f"<div class='btnrow'><button>新增</button></div></form></details></section>"]
        items = []
        for r in all_rules:
            g = f"r{r['id']}"
            names_txt = "：" + "、".join(who.get(u, str(u)) for u in r["users"]) if r["users"] else ""
            words = "/".join(rules.split_words(r["words"])) or "不限關鍵字"
            items.append(
                f"<li data-id='{r['id']}' id='{g}' class='{'' if r['on'] else 'off'}'>"
                f"<span class='handle' title='拖曳調整順序'>⋮⋮</span>"
                f"<input type='checkbox' class='rtoggle' title='啟用' {'checked' if r['on'] else ''}>"
                f"<details {'open' if draft and draft['id'] == r['id'] else ''}><summary><b>{escape(r['name'])}</b> "
                f"<span class='kw'>{escape(words)}</span><span class='tag'>{escape(rules.summary(r))}</span></summary>"
                f"<form method='post' action='/rules/{r['id']}'>{rule_fields(r, escape(names_txt))}"
                f"<div class='btnrow'><button>儲存</button></div></form>"
                f"<label>圖片：符合時隨機附一張</label>{gallery(g, bot.core.images(g), 'rules')}"
                f"<form method='post' action='/rules/{r['id']}/delete' style='margin-top:10px' "
                f"onsubmit=\"return confirm('刪掉這條規則和它的圖？')\"><button class='ghost small'>刪除這條規則</button></form>"
                f"</details></li>")
        out.append(f"<section><h2>自訂回覆（{len(all_rules)} 條）<span class='toast' id='rtoast'></span></h2>"
                   + (f"<ol class='rulelist' id='rulelist'>{''.join(items)}</ol>" if items
                      else "<p class='tag'>還沒有規則</p>") + "</section>")
        out.append(RULES_JS)
        return page("自訂回覆", "rules", "".join(out), flash, error)

    async def rules_get(request: web.Request):
        return await rules_page(request)

    async def rule_save(request: web.Request):
        form = dict(await request.post())
        all_rules = rules.load(bot.store)
        rid = request.match_info.get("id")
        if rid is None:
            rid = max([int(bot.store.get_meta("rule_seq") or 0)] + [r["id"] for r in all_rules]) + 1
        rid = int(rid)
        rule, errors = rules.from_form(form, rid)
        if errors:
            return await rules_page(request, "沒存到：" + escape("；".join(errors)), True,
                                    rule if any(r["id"] == rid for r in all_rules) else None)
        missing = missing_codes({"replies": rule["replies"]})
        if any(r["id"] == rid for r in all_rules):
            all_rules = [rule if r["id"] == rid else r for r in all_rules]
        else:
            all_rules.append(rule)
            bot.store.set_meta("rule_seq", rid)
        rules.save(bot.store, all_rules)
        if missing:
            raise web.HTTPSeeOther("/rules?" + urlencode(
                {"err": "已儲存，但圖片頁找不到這些代碼：" + "、".join(f"{{圖:{c}}}" for c in missing)}) + f"#r{rid}")
        raise web.HTTPSeeOther(f"/rules?ok=1#r{rid}")

    async def rule_delete(request: web.Request):
        rid = int(request.match_info["id"])
        rules.save(bot.store, [r for r in rules.load(bot.store) if r["id"] != rid])
        images.delete_group(img_root(), f"r{rid}")
        bot.core.set_images(f"r{rid}", [])
        raise web.HTTPSeeOther("/rules?ok=1")

    async def rule_toggle(request: web.Request):
        rid, form = int(request.match_info["id"]), await request.post()
        all_rules = rules.load(bot.store)
        for r in all_rules:
            if r["id"] == rid:
                r["on"] = form.get("on") == "1"
        rules.save(bot.store, all_rules)
        return web.Response(status=204)

    async def rule_order(request: web.Request):
        """拖曳完送來新的順序（規則編號，逗號分開）；沒列到的照原順序接在後面。"""
        form = await request.post()
        want = [int(x) for x in str(form.get("ids", "")).split(",") if x.strip().isdigit()]
        all_rules = rules.load(bot.store)
        by_id = {r["id"]: r for r in all_rules}
        ordered = [by_id[i] for i in dict.fromkeys(want) if i in by_id]
        rules.save(bot.store, ordered + [r for r in all_rules if r["id"] not in want])
        return web.Response(status=204)

    # ---------- 更新程式 ----------
    data = updater.data_dir(cfg.db_path)

    def when(ts) -> str:
        return datetime.fromtimestamp(ts, bot.tz).strftime("%m/%d %H:%M") if ts else "?"

    async def update_page(request: web.Request):
        cur, st, busy = updater.version(data), updater.status(data), updater.running(data)
        new = await updater.latest(cur.get("remote", ""), cur.get("branch", "main")) if cur else None
        flash, err = "", False
        if request.query.get("busy"):
            flash, err = "正在更新，等它跑完再按。", True
        elif busy:
            flash = "⏳ 更新中…大約 1 分鐘，頁面會自己重新整理。"
        out = ["<section><h2>主機現在跑的版本</h2>"]
        if cur and cur.get("commit"):
            out.append(f"<p><code>{escape(cur['short'])}</code> {escape(cur.get('message', ''))}"
                       f"<br><span class='tag'>commit 時間 {when(cur.get('time'))}・安裝於 {when(cur.get('installed_at'))}"
                       f"</span></p>")
        else:
            out.append("<p class='tag'>不知道。在主機上跑一次 <code>sudo bash deploy/install.sh</code> 就會記錄。</p>")
        out.append("</section><section><h2>GitHub 上最新的版本</h2>")
        if new:
            newer = bool(cur and cur.get("commit") and new["commit"] != cur["commit"])
            mark = " <b class='badge'>有新版本</b>" if newer else " <span class='tag'>已經是最新版</span>"
            out.append(f"<p><code>{escape(new['short'])}</code> {escape(new['message'])}{mark}"
                       f"<br><span class='tag'>commit 時間 {when(new['time'])}</span></p>")
        else:
            out.append("<p class='tag'>查不到（GitHub 沒回應，或還不知道 repo 在哪）</p>")
        out.append("</section><section><h2>上次更新</h2>")
        if st:
            mark = "⏳" if st.get("state") == "running" else ("✅" if st.get("ok") else "❌")
            ver = f"{st.get('from') or '?'} → {st.get('to')}" if st.get("to") and st.get("to") != st.get("from") else ""
            out.append(f"<p>{mark} {escape(st.get('message', ''))}<br><span class='tag'>"
                       f"{when(st.get('finished') or st.get('started'))}{'・' + escape(ver) if ver else ''}</span></p>")
        else:
            out.append("<p class='tag'>還沒從網頁更新過</p>")
        if log := updater.log_tail(data):
            out.append(f"<details><summary class='tag'>看紀錄</summary><pre class='log'>{escape(log)}</pre></details>")
        out.append("</section>")
        out.append("<form method='post' action='/update' onsubmit=\"return confirm('更新到 GitHub 上最新的版本？"
                   "賈維斯會重新啟動，大約 1 分鐘。新版起不來會自動退回。')\"><div class='btnrow'>"
                   f"<button {'disabled' if busy else ''}>{'更新中…' if busy else '更新到最新版'}</button></div></form>")
        resp = page("更新", "update", "".join(out), flash, err)
        if busy:
            resp.headers["Refresh"] = "15"
        return resp

    async def update_start(request: web.Request):
        if not updater.request(data):
            raise web.HTTPSeeOther("/update?busy=1")
        raise web.HTTPSeeOther("/update")

    async def update_json(request: web.Request):
        """右上角的一鍵更新按鈕用：現在版本、GitHub 最新版本、是不是在更新中、上次結果。"""
        cur, st = updater.version(data) or {}, updater.status(data) or {}
        new = await updater.latest(cur.get("remote", ""), cur.get("branch", "main")) if cur else None
        return web.json_response({
            "current": cur.get("short"), "latest": new["short"] if new else None,
            "newer": bool(new and cur.get("commit") and new["commit"] != cur["commit"]),
            "running": updater.running(data), "last_ok": st.get("ok"), "last": st.get("message")})

    async def health(request: web.Request):
        return web.json_response({"ok": True, "discord_ready": bots[0].is_ready(),
                                  "bots": {b.config.key: b.is_ready() for b in bots}})

    app = web.Application(middlewares=[guard], client_max_size=80 * 1024 * 1024)
    routes = [
        web.get("/", settings_page), web.post("/", save_settings), web.post("/reset", reset_settings),
        web.get("/food", food_page), web.post("/food", save_food), web.post("/food/reset", reset_food),
        web.get("/replies", replies_page), web.post("/replies", save_replies), web.post("/replies/reset", reset_replies),
        web.post("/weather/test", weather_test),
        web.get("/reminders", reminders_page), web.post(r"/reminders/{id:\d+}/cancel", reminder_cancel),
        web.post(r"/reminders/{id:\d+}/edit", reminder_edit),
        web.get("/notes", notes_page), web.post(r"/notes/{id:\d+}/delete", note_delete),
        web.post(r"/notes/{id:\d+}/edit", note_edit), web.post("/notes/add", note_add),
        web.get("/rules", rules_get), web.post("/rules/new", rule_save), web.post(r"/rules/{id:\d+}", rule_save),
        web.post(r"/rules/{id:\d+}/delete", rule_delete), web.post(r"/rules/{id:\d+}/toggle", rule_toggle),
        web.post("/rules/order", rule_order),
        web.get("/images", images_page), web.post(r"/images/{group}/upload", image_upload),
        web.get(r"/images/{group}/{name}", image_file), web.post(r"/images/{group}/{name}/delete", image_delete),
        web.post(r"/images/{group}/{name}/replace", image_replace),
        web.get("/logic", logic_page), web.get("/update", update_page), web.post("/update", update_start),
        web.get("/update.json", update_json), web.get("/reactions", reactions_page), web.post("/reactions", save_reactions),
        web.post("/reactions/reset", reset_reactions),
    ]
    for pre in prefixes.values():
        for r in routes:
            app.router.add_route(r.method, (pre + r.path) if r.path != "/" else (pre + "/" if pre else "/"), r.handler)
    app.router.add_get("/healthz", health)

    return app


REPLY_HELP = ("<p class='tag'>一行一句，隨機挑一句回。想回多行的話用 \" \" 把那句包起來，中間可以換行。"
              "句子後面加 {圖:代碼} 就一定附那張圖，代碼在這頁下面或自訂回覆的圖下面；加 {圖:無} 就這句不附圖。</p>")


RULES_JS = """<script>
(() => {
  const list = document.getElementById("rulelist"), toast = document.getElementById("rtoast");
  const say = t => { if (toast) { toast.textContent = t; setTimeout(() => { toast.textContent = ""; }, 2000); } };
  const B = document.body.dataset.base || "";
  const post = (url, data) => fetch(B + url, {method: "POST", body: new URLSearchParams(data)})
    .then(r => { if (!r.ok) throw new Error(r.status); });
  const hash = location.hash && document.querySelector(location.hash + " > details");
  if (hash) hash.open = true;
  if (!list) return;
  list.addEventListener("change", e => {
    if (!e.target.matches(".rtoggle")) return;
    const li = e.target.closest("li"), on = e.target.checked;
    post(`/rules/${li.dataset.id}/toggle`, {on: on ? "1" : ""})
      .then(() => { li.classList.toggle("off", !on); say(on ? "已啟用" : "已停用"); })
      .catch(() => { e.target.checked = !on; say("沒存到，重新整理再試"); });
  });
  // 按住 ⋮⋮ 拖曳：滑鼠、手指都可以
  let drag = null, before = "";
  const order = () => [...list.children].map(li => li.dataset.id).join(",");
  list.addEventListener("pointerdown", e => {
    const h = e.target.closest(".handle"); if (!h) return;
    e.preventDefault(); h.setPointerCapture(e.pointerId);
    drag = h.closest("li"); drag.classList.add("dragging"); before = order();
  });
  list.addEventListener("pointermove", e => {
    if (!drag) return;
    const next = [...list.children].find(li => li !== drag &&
      e.clientY < li.getBoundingClientRect().top + li.getBoundingClientRect().height / 2);
    if (next !== drag.nextElementSibling) list.insertBefore(drag, next || null);
  });
  const end = () => {
    if (!drag) return;
    drag.classList.remove("dragging"); drag = null;
    const now = order();
    if (now !== before) post("/rules/order", {ids: now}).then(() => say("順序已更新")).catch(() => say("沒存到，重新整理再試"));
  };
  list.addEventListener("pointerup", end); list.addEventListener("pointercancel", end);
})();
</script>"""

PAGES = {"settings": ("設定", GROUPS, "/"), "replies": ("預設回覆", REPLY_GROUPS, "/replies"),
         "food": ("吃什麼", FOOD_GROUPS, "/food"), "reactions": ("表情符號", REACTION_GROUPS, "/reactions")}
KEYS = {"settings": SETTING_KEYS, "replies": REPLY_KEYS, "food": FOOD_KEYS, "reactions": REACTION_KEYS}
REACTION_HELP = ("<p class='tag'>看到關鍵字就幫那則訊息按表符，不管有沒有叫它。一行一組「關鍵字/關鍵字｜表符 表符」，"
                 "[ ] 裡的字任一個都算，例如 [雞機][吧巴]；表符寫 :名稱: 或直接放 emoji。比對前會去掉空白和標點。</p>")


def render_form(s: dict, groups, path: str, tools: bool, tall: bool = False) -> str:
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
                rows = min(30 if tall else 8, max(2, str(v).count("\n") + 1))
                out.append(f"<textarea id='{k}' name='{k}' rows='{rows}'>{escape(str(v))}</textarea>")
            elif isinstance(DEFAULTS[k], int):
                out.append(f"<input type='number' id='{k}' name='{k}' value='{v}'>")
            else:
                out.append(f"<input type='text' id='{k}' name='{k}' value='{escape(str(v), quote=True)}'>")
        out.append("</section>")
    # 儲存、恢復預設、貼一次天氣放同一列（formaction 送到不同網址），不另外開一個區塊
    reset = "/reset" if path == "/" else f"{path}/reset"
    out.append("<div class='bar'><button>儲存</button><span class='sp'></span>"
               + ("<button class='ghost' formaction='/weather/test' formnovalidate>貼一次天氣</button>" if tools else "")
               + f"<button class='ghost' formaction='{reset}' formnovalidate "
               "onclick=\"return confirm('這一頁全部恢復預設？')\">恢復預設</button></div></form>")
    return "".join(out)


async def start_web(bots) -> web.AppRunner:
    bots = list(bots) if isinstance(bots, (list, tuple)) else [bots]
    cfg = bots[0].config
    runner = web.AppRunner(make_app(bots), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, cfg.web_host, cfg.web_port)
    await site.start()
    log.info("設定網頁：http://%s:%s", cfg.web_host, cfg.web_port)
    return runner
