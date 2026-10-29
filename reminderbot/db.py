"""SQLite 資料存取。時間一律存 UTC 的 epoch 秒數。"""
from __future__ import annotations

import json
import time as _time

import aiosqlite

from .settings import DEFAULTS

SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL DEFAULT 'reminder',   -- reminder / timer
    channel_id  INTEGER NOT NULL,
    creator_id  INTEGER NOT NULL,
    targets     TEXT NOT NULL,                      -- JSON：["u:123", "r:456"]
    text        TEXT NOT NULL,
    due_at      INTEGER NOT NULL,
    repeat      TEXT,                               -- 有值 = 重複提醒的本體
    parent_id   INTEGER,                            -- 重複提醒觸發時產生的那一次
    status      TEXT NOT NULL DEFAULT 'pending',    -- pending / fired / done / cancelled
    message_id  INTEGER,
    nag_count   INTEGER NOT NULL DEFAULT 0,
    fired_at    INTEGER,
    extra       TEXT,                               -- JSON，例如計時器長度
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rem_due ON reminders(status, due_at);

CREATE TABLE IF NOT EXISTS notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    category    TEXT NOT NULL,
    text        TEXT NOT NULL,
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(user_id, category);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class DB:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None
        self._settings: dict | None = None

    async def open(self):
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.commit()

    async def close(self):
        if self.conn:
            await self.conn.close()

    # ---------- 設定 ----------
    async def settings(self) -> dict:
        if self._settings is None:
            rows = await self.conn.execute_fetchall("SELECT key, value FROM settings")
            stored = {r["key"]: json.loads(r["value"]) for r in rows}
            self._settings = {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}
        return dict(self._settings)

    async def save_settings(self, values: dict):
        await self.conn.executemany(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            [(k, json.dumps(v, ensure_ascii=False)) for k, v in values.items()])
        await self.conn.commit()
        self._settings = None

    async def get_meta(self, key: str, default=None):
        rows = await self.conn.execute_fetchall("SELECT value FROM settings WHERE key = ?", ("_" + key,))
        return json.loads(rows[0]["value"]) if rows else default

    async def set_meta(self, key: str, value):
        await self.conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("_" + key, json.dumps(value, ensure_ascii=False)))
        await self.conn.commit()

    # ---------- 提醒 ----------
    async def add_reminder(self, *, channel_id: int, creator_id: int, targets: list[str], text: str,
                           due_at: int, repeat: str | None = None, kind: str = "reminder",
                           parent_id: int | None = None, status: str = "pending",
                           extra: dict | None = None) -> int:
        cur = await self.conn.execute(
            "INSERT INTO reminders(kind, channel_id, creator_id, targets, text, due_at, repeat, "
            "parent_id, status, extra, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (kind, channel_id, creator_id, json.dumps(targets), text, due_at, repeat, parent_id,
             status, json.dumps(extra or {}), int(_time.time())))
        await self.conn.commit()
        return cur.lastrowid

    async def get_reminder(self, rid: int) -> dict | None:
        rows = await self.conn.execute_fetchall("SELECT * FROM reminders WHERE id = ?", (rid,))
        return _rem(rows[0]) if rows else None

    async def reminder_by_message(self, message_id: int) -> dict | None:
        rows = await self.conn.execute_fetchall(
            "SELECT * FROM reminders WHERE message_id = ? ORDER BY id DESC LIMIT 1", (message_id,))
        return _rem(rows[0]) if rows else None

    async def update_reminder(self, rid: int, **fields):
        if "targets" in fields:
            fields["targets"] = json.dumps(fields["targets"])
        sets = ", ".join(f"{k} = ?" for k in fields)
        await self.conn.execute(f"UPDATE reminders SET {sets} WHERE id = ?", (*fields.values(), rid))
        await self.conn.commit()

    async def due_reminders(self, now: int) -> list[dict]:
        rows = await self.conn.execute_fetchall(
            "SELECT * FROM reminders WHERE status = 'pending' AND due_at <= ? ORDER BY due_at", (now,))
        return [_rem(r) for r in rows]

    async def nag_candidates(self, before: int, max_count: int) -> list[dict]:
        rows = await self.conn.execute_fetchall(
            "SELECT * FROM reminders WHERE status = 'fired' AND kind = 'reminder' "
            "AND nag_count < ? AND fired_at <= ?", (max_count, before))
        return [_rem(r) for r in rows]

    async def user_reminders(self, user_id: int) -> list[dict]:
        """這個人建立的、或提醒對象是他的、還沒結束的提醒。"""
        rows = await self.conn.execute_fetchall(
            "SELECT * FROM reminders WHERE status IN ('pending', 'fired') "
            "AND (creator_id = ? OR targets LIKE ?) ORDER BY due_at",
            (user_id, f'%"u:{user_id}"%'))
        return [_rem(r) for r in rows]

    async def all_reminders(self, statuses: tuple[str, ...], limit: int = 300) -> list[dict]:
        q = ",".join("?" * len(statuses))
        rows = await self.conn.execute_fetchall(
            f"SELECT * FROM reminders WHERE status IN ({q}) ORDER BY "
            f"CASE WHEN status IN ('pending','fired') THEN due_at END ASC, due_at DESC LIMIT ?",
            (*statuses, limit))
        return [_rem(r) for r in rows]

    # ---------- 記事 ----------
    async def add_note(self, user_id: int, category: str, text: str) -> int:
        cur = await self.conn.execute(
            "INSERT INTO notes(user_id, category, text, created_at) VALUES(?,?,?,?)",
            (user_id, category, text, int(_time.time())))
        await self.conn.commit()
        return cur.lastrowid

    async def user_notes(self, user_id: int, category: str | None = None) -> list[dict]:
        if category:
            rows = await self.conn.execute_fetchall(
                "SELECT * FROM notes WHERE user_id = ? AND category = ? ORDER BY id", (user_id, category))
        else:
            rows = await self.conn.execute_fetchall(
                "SELECT * FROM notes WHERE user_id = ? ORDER BY category, id", (user_id,))
        return [dict(r) for r in rows]

    async def all_notes(self, limit: int = 500) -> list[dict]:
        rows = await self.conn.execute_fetchall("SELECT * FROM notes ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    async def delete_note(self, note_id: int, user_id: int | None = None) -> bool:
        if user_id is None:
            cur = await self.conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        else:
            cur = await self.conn.execute("DELETE FROM notes WHERE id = ? AND user_id = ?", (note_id, user_id))
        await self.conn.commit()
        return cur.rowcount > 0


def _rem(row) -> dict:
    d = dict(row)
    d["targets"] = json.loads(d["targets"])
    d["extra"] = json.loads(d["extra"] or "{}")
    return d
