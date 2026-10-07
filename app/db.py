"""Tiny SQLite store for calls and leads.

SQLite is plenty for a few thousand calls a month on one server. Keep the
database file on a persistent volume (see README). To scale out to several
servers, swap this module for Postgres — the rest of the app only uses the
functions below.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    call_sid        TEXT PRIMARY KEY,
    client_id       TEXT NOT NULL,
    from_number     TEXT,
    to_number       TEXT,
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    duration_sec    INTEGER,
    status          TEXT DEFAULT 'in_progress',   -- in_progress | completed | transferred | failed
    end_reason      TEXT,
    lead_json       TEXT DEFAULT '{}',
    score           INTEGER,
    tier            TEXT,
    score_reasons   TEXT,
    summary         TEXT,
    next_step       TEXT,
    appointment_at  TEXT,
    booking_uid     TEXT,
    transferred     INTEGER DEFAULT 0,
    transcript_json TEXT DEFAULT '[]',
    recording_url   TEXT,
    crm_status      TEXT,
    alert_status    TEXT
);
CREATE INDEX IF NOT EXISTS idx_calls_client_started ON calls(client_id, started_at DESC);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._lock = threading.Lock()

    # ---------- sync core ----------
    def _execute(self, sql: str, args: tuple = ()) -> None:
        with self._lock:
            self._conn.execute(sql, args)
            self._conn.commit()

    def _query(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    # ---------- async API ----------
    async def call_started(self, call_sid: str, client_id: str, from_number: str, to_number: str) -> None:
        await asyncio.to_thread(
            self._execute,
            "INSERT OR IGNORE INTO calls(call_sid, client_id, from_number, to_number, started_at) VALUES (?,?,?,?,?)",
            (call_sid, client_id, from_number, to_number, utcnow()),
        )

    async def update_call(self, call_sid: str, **fields) -> None:
        if not fields:
            return
        cols, vals = [], []
        for k, v in fields.items():
            if isinstance(v, (dict, list)):
                v = json.dumps(v)
            cols.append(f"{k}=?")
            vals.append(v)
        vals.append(call_sid)
        await asyncio.to_thread(self._execute, f"UPDATE calls SET {', '.join(cols)} WHERE call_sid=?", tuple(vals))

    async def get_call(self, call_sid: str) -> dict | None:
        rows = await asyncio.to_thread(self._query, "SELECT * FROM calls WHERE call_sid=?", (call_sid,))
        return _decode(rows[0]) if rows else None

    async def list_calls(self, client_id: str | None = None, tier: str | None = None, limit: int = 200) -> list[dict]:
        sql, args = "SELECT * FROM calls WHERE 1=1", []
        if client_id:
            sql += " AND client_id=?"; args.append(client_id)
        if tier:
            sql += " AND tier=?"; args.append(tier)
        sql += " ORDER BY started_at DESC LIMIT ?"; args.append(limit)
        rows = await asyncio.to_thread(self._query, sql, tuple(args))
        return [_decode(r) for r in rows]

    async def stats(self, client_id: str | None = None) -> dict:
        sql = "SELECT tier, COUNT(*) AS n FROM calls"
        args: tuple = ()
        if client_id:
            sql += " WHERE client_id=?"; args = (client_id,)
        sql += " GROUP BY tier"
        rows = await asyncio.to_thread(self._query, sql, args)
        out = {r["tier"] or "unscored": r["n"] for r in rows}
        out["total"] = sum(out.values())
        booked_sql = "SELECT COUNT(*) AS n FROM calls WHERE booking_uid IS NOT NULL" + (" AND client_id=?" if client_id else "")
        out["booked"] = (await asyncio.to_thread(self._query, booked_sql, args))[0]["n"]
        return out


def _decode(row: dict) -> dict:
    for k in ("lead_json", "transcript_json", "score_reasons"):
        if row.get(k):
            try:
                row[k] = json.loads(row[k])
            except (TypeError, json.JSONDecodeError):
                pass
    return row
