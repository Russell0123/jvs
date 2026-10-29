"""資料存取。兩種實作、同一組方法：
- SqliteStore：正式上線用（標準函式庫 sqlite3）
- MemoryStore：測試網頁（瀏覽器裡跑的 Python）用，資料只在記憶體

時間一律是 UTC epoch 秒數，由呼叫端傳入 now，方便測試網頁快轉時間。
"""
from __future__ import annotations

import json

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
    created_at  INTEGER NOT NULL,
    mentioned_at INTEGER,                           -- 最後一次提到（願望清單挑選用）
    mentions    INTEGER NOT NULL DEFAULT 1,         -- 提過幾次
    credit      REAL NOT NULL DEFAULT 0             -- 輪流挑選的累積分數
);
CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(user_id, category);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

ACTIVE = ("pending", "fired")


def _merge_settings(stored: dict) -> dict:
    return {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}


def _sort_all(rems: list[dict]) -> list[dict]:
    active = sorted((r for r in rems if r["status"] in ACTIVE), key=lambda r: r["due_at"])
    ended = sorted((r for r in rems if r["status"] not in ACTIVE), key=lambda r: -r["due_at"])
    return active + ended


class MemoryStore:
    def __init__(self):
        self._rem: dict[int, dict] = {}
        self._notes: dict[int, dict] = {}
        self._settings: dict = {}
        self._meta: dict = {}
        self._rid = self._nid = 0

    # 設定
    def settings(self) -> dict:
        return _merge_settings(self._settings)

    def save_settings(self, values: dict):
        self._settings.update(values)

    def get_meta(self, key, default=None):
        return self._meta.get(key, default)

    def set_meta(self, key, value):
        self._meta[key] = value

    # 提醒
    def add_reminder(self, *, channel_id, creator_id, targets, text, due_at, now, repeat=None,
                     kind="reminder", parent_id=None, status="pending", extra=None) -> int:
        self._rid += 1
        self._rem[self._rid] = dict(
            id=self._rid, kind=kind, channel_id=channel_id, creator_id=creator_id, targets=list(targets),
            text=text, due_at=int(due_at), repeat=repeat, parent_id=parent_id, status=status, message_id=None,
            nag_count=0, fired_at=None, extra=dict(extra or {}), created_at=int(now))
        return self._rid

    def get_reminder(self, rid):
        r = self._rem.get(rid)
        return _copy(r) if r else None

    def reminder_by_message(self, message_id):
        hits = [r for r in self._rem.values() if r["message_id"] == message_id]
        return _copy(max(hits, key=lambda r: r["id"])) if hits else None

    def update_reminder(self, rid, **fields):
        if rid in self._rem:
            self._rem[rid].update(fields)

    def due_reminders(self, now):
        return [_copy(r) for r in sorted(self._rem.values(), key=lambda r: r["due_at"])
                if r["status"] == "pending" and r["due_at"] <= now]

    def nag_candidates(self, before, max_count):
        return [_copy(r) for r in self._rem.values()
                if r["status"] == "fired" and r["kind"] == "reminder" and r["nag_count"] < max_count
                and (r["fired_at"] or 0) <= before]

    def user_reminders(self, user_id):
        return [_copy(r) for r in sorted(self._rem.values(), key=lambda r: r["due_at"])
                if r["status"] in ACTIVE and (r["creator_id"] == user_id or f"u:{user_id}" in r["targets"])]

    def all_reminders(self, statuses, limit=300):
        return [_copy(r) for r in _sort_all([r for r in self._rem.values() if r["status"] in statuses])][:limit]

    # 記事
    def add_note(self, user_id, category, text, now) -> int:
        self._nid += 1
        self._notes[self._nid] = dict(id=self._nid, user_id=user_id, category=category, text=text,
                                      created_at=int(now), mentioned_at=int(now), mentions=1, credit=0.0)
        return self._nid

    def get_note(self, note_id):
        n = self._notes.get(note_id)
        return dict(n) if n else None

    def update_note(self, note_id, **fields):
        if note_id in self._notes:
            self._notes[note_id].update(fields)

    def user_notes(self, user_id, category=None):
        rows = [dict(n) for n in self._notes.values()
                if n["user_id"] == user_id and (category is None or n["category"] == category)]
        return rows if category else sorted(rows, key=lambda n: (n["category"], n["id"]))

    def all_notes(self, limit=1000):
        return [dict(n) for n in sorted(self._notes.values(), key=lambda n: -n["id"])][:limit]

    def delete_note(self, note_id, user_id=None) -> bool:
        n = self._notes.get(note_id)
        if not n or (user_id is not None and n["user_id"] != user_id):
            return False
        del self._notes[note_id]
        return True


def _copy(r: dict) -> dict:
    return {**r, "targets": list(r["targets"]), "extra": dict(r["extra"])}


class SqliteStore:
    def __init__(self, path: str):
        import sqlite3
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        # 舊資料庫補欄位
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(notes)")}
        for col, ddl in [("mentioned_at", "INTEGER"), ("mentions", "INTEGER NOT NULL DEFAULT 1"),
                         ("credit", "REAL NOT NULL DEFAULT 0")]:
            if col not in cols:
                self.conn.execute(f"ALTER TABLE notes ADD COLUMN {col} {ddl}")
        self.conn.execute("UPDATE notes SET mentioned_at = created_at WHERE mentioned_at IS NULL")
        self.conn.commit()
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.commit()
        self._cache: dict | None = None

    def close(self):
        self.conn.close()

    def _all(self, sql, args=()):
        return self.conn.execute(sql, args).fetchall()

    def _write(self, sql, args=()):
        cur = self.conn.execute(sql, args)
        self.conn.commit()
        return cur

    # 設定（存在 settings 表；_ 開頭的 key 是內部狀態）
    def settings(self) -> dict:
        if self._cache is None:
            rows = self._all("SELECT key, value FROM settings WHERE key NOT LIKE '\\_%' ESCAPE '\\'")
            self._cache = _merge_settings({r["key"]: json.loads(r["value"]) for r in rows})
        return dict(self._cache)

    def save_settings(self, values: dict):
        self.conn.executemany(
            "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            [(k, json.dumps(v, ensure_ascii=False)) for k, v in values.items()])
        self.conn.commit()
        self._cache = None

    def get_meta(self, key, default=None):
        rows = self._all("SELECT value FROM settings WHERE key = ?", ("_" + key,))
        return json.loads(rows[0]["value"]) if rows else default

    def set_meta(self, key, value):
        self._write("INSERT INTO settings(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    ("_" + key, json.dumps(value, ensure_ascii=False)))

    # 提醒
    def add_reminder(self, *, channel_id, creator_id, targets, text, due_at, now, repeat=None,
                     kind="reminder", parent_id=None, status="pending", extra=None) -> int:
        cur = self._write(
            "INSERT INTO reminders(kind, channel_id, creator_id, targets, text, due_at, repeat, "
            "parent_id, status, extra, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (kind, channel_id, creator_id, json.dumps(targets), text, int(due_at), repeat, parent_id,
             status, json.dumps(extra or {}, ensure_ascii=False), int(now)))
        return cur.lastrowid

    def get_reminder(self, rid):
        rows = self._all("SELECT * FROM reminders WHERE id = ?", (rid,))
        return _row(rows[0]) if rows else None

    def reminder_by_message(self, message_id):
        rows = self._all("SELECT * FROM reminders WHERE message_id = ? ORDER BY id DESC LIMIT 1", (message_id,))
        return _row(rows[0]) if rows else None

    def update_reminder(self, rid, **fields):
        if "targets" in fields:
            fields["targets"] = json.dumps(fields["targets"])
        if "extra" in fields:
            fields["extra"] = json.dumps(fields["extra"], ensure_ascii=False)
        sets = ", ".join(f"{k} = ?" for k in fields)
        self._write(f"UPDATE reminders SET {sets} WHERE id = ?", (*fields.values(), rid))

    def due_reminders(self, now):
        return [_row(r) for r in self._all(
            "SELECT * FROM reminders WHERE status = 'pending' AND due_at <= ? ORDER BY due_at", (now,))]

    def nag_candidates(self, before, max_count):
        return [_row(r) for r in self._all(
            "SELECT * FROM reminders WHERE status = 'fired' AND kind = 'reminder' "
            "AND nag_count < ? AND COALESCE(fired_at, 0) <= ?", (max_count, before))]

    def user_reminders(self, user_id):
        return [_row(r) for r in self._all(
            "SELECT * FROM reminders WHERE status IN ('pending', 'fired') "
            "AND (creator_id = ? OR targets LIKE ?) ORDER BY due_at", (user_id, f'%"u:{user_id}"%'))]

    def all_reminders(self, statuses, limit=300):
        q = ",".join("?" * len(statuses))
        return _sort_all([_row(r) for r in self._all(
            f"SELECT * FROM reminders WHERE status IN ({q})", tuple(statuses))])[:limit]

    # 記事
    def add_note(self, user_id, category, text, now) -> int:
        return self._write("INSERT INTO notes(user_id, category, text, created_at, mentioned_at) VALUES(?,?,?,?,?)",
                           (user_id, category, text, int(now), int(now))).lastrowid

    def get_note(self, note_id):
        rows = self._all("SELECT * FROM notes WHERE id = ?", (note_id,))
        return dict(rows[0]) if rows else None

    def update_note(self, note_id, **fields):
        sets = ", ".join(f"{k} = ?" for k in fields)
        self._write(f"UPDATE notes SET {sets} WHERE id = ?", (*fields.values(), note_id))

    def user_notes(self, user_id, category=None):
        if category:
            rows = self._all("SELECT * FROM notes WHERE user_id = ? AND category = ? ORDER BY id",
                             (user_id, category))
        else:
            rows = self._all("SELECT * FROM notes WHERE user_id = ? ORDER BY category, id", (user_id,))
        return [dict(r) for r in rows]

    def all_notes(self, limit=1000):
        return [dict(r) for r in self._all("SELECT * FROM notes ORDER BY id DESC LIMIT ?", (limit,))]

    def delete_note(self, note_id, user_id=None) -> bool:
        if user_id is None:
            cur = self._write("DELETE FROM notes WHERE id = ?", (note_id,))
        else:
            cur = self._write("DELETE FROM notes WHERE id = ? AND user_id = ?", (note_id, user_id))
        return cur.rowcount > 0


def _row(row) -> dict:
    d = dict(row)
    d["targets"] = json.loads(d["targets"])
    d["extra"] = json.loads(d["extra"] or "{}")
    return d
