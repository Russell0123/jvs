"""設定網頁的「更新到最新版」：只讀版本／狀態檔、寫觸發檔，真正的更新由 root 的 jvs-update.service 做。

/opt/jvs/VERSION.json        install.sh 寫的，現在跑的版本
/opt/jvs/data/update-request 觸發檔：bot 寫，jvs-update.path 看到就開始更新，跑完刪掉
/opt/jvs/data/update-status.json  deploy/web-update.sh 寫的上次結果
/opt/jvs/data/update.log     更新的完整紀錄
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime
from pathlib import Path

import aiohttp

STALE = 20 * 60          # 「更新中」超過 20 分鐘沒結束，就當它已經停了，可以再按


def data_dir(db_path: str) -> Path:
    return Path(db_path).resolve().parent


def _json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def version(data: Path) -> dict | None:
    return _json(data.parent / "VERSION.json")


def status(data: Path) -> dict | None:
    return _json(data / "update-status.json")


def running(data: Path, now: float | None = None) -> bool:
    now = now or time.time()
    req = data / "update-request"
    if req.exists() and now - req.stat().st_mtime < STALE:
        return True
    st = status(data)
    return bool(st and st.get("state") == "running" and now - (st.get("started") or 0) < STALE)


def request(data: Path) -> bool:
    """寫觸發檔；已經在更新就回 False。"""
    if running(data):
        return False
    (data / "update-request").write_text(str(int(time.time())))
    return True


def log_tail(data: Path, lines: int = 40) -> str:
    try:
        return "\n".join((data / "update.log").read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def github_repo(remote: str) -> str | None:
    """https://github.com/a/b.git 或 git@github.com:a/b → a/b"""
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$", remote or "")
    return m.group(1) if m else None


_cache: dict = {}


async def latest(remote: str, branch: str = "main") -> dict | None:
    """GitHub 上最新的 commit：{commit, short, message, time}；查不到回 None。60 秒內不重查。"""
    repo = github_repo(remote)
    if not repo:
        return None
    key = (repo, branch)
    if key in _cache and time.time() - _cache[key][0] < 60:
        return _cache[key][1]
    out = None
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6), trust_env=True) as s:
            async with s.get(f"https://api.github.com/repos/{repo}/commits/{branch}",
                             headers={"Accept": "application/vnd.github+json", "User-Agent": "jvs"}) as r:
                if r.status == 200:
                    d = await r.json()
                    t = d["commit"]["committer"]["date"]
                    out = {"commit": d["sha"], "short": d["sha"][:7],
                           "message": d["commit"]["message"].splitlines()[0],
                           "time": int(datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp())}
    except (aiohttp.ClientError, asyncio.TimeoutError, KeyError, ValueError):
        out = None
    _cache[key] = (time.time(), out)
    return out
