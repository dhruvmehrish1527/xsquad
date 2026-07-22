"""SQLite persistence (SW-04, O-4): cache, squad history, settings."""
import json
import os
import sqlite3
import time
from pathlib import Path

# XSQUAD_DB overrides the location so deployments can point at a mounted
# persistent volume (e.g. /data/fpl_optimizer.db on Fly.io).
DB_PATH = Path(os.environ.get(
    "XSQUAD_DB",
    Path(__file__).resolve().parent.parent / "fpl_optimizer.db"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    fetched_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS squads (
    gameweek INTEGER PRIMARY KEY,
    source TEXT NOT NULL,           -- 'import' | 'generated'
    payload TEXT NOT NULL,          -- JSON: picks, bank, free_transfers
    saved_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(_SCHEMA)
    return conn


# ---------------- cache (FR-DATA-05) ----------------

def cache_get(key: str, max_age: float | None = None):
    with _conn() as c:
        row = c.execute("SELECT payload, fetched_at FROM cache WHERE key=?", (key,)).fetchone()
    if row is None:
        return None
    payload, fetched_at = row
    if max_age is not None and time.time() - fetched_at > max_age:
        return None
    return json.loads(payload)


def cache_put(key: str, payload) -> None:
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO cache(key, payload, fetched_at) VALUES (?,?,?)",
            (key, json.dumps(payload), time.time()),
        )


def cache_get_many(prefix: str) -> dict:
    """Bulk read of all cache entries whose key starts with prefix."""
    with _conn() as c:
        rows = c.execute("SELECT key, payload FROM cache WHERE key LIKE ?",
                         (prefix + "%",)).fetchall()
    return {k: json.loads(p) for k, p in rows}


def cache_age(key: str) -> float | None:
    with _conn() as c:
        row = c.execute("SELECT fetched_at FROM cache WHERE key=?", (key,)).fetchone()
    return None if row is None else time.time() - row[0]


# ---------------- squads (FR-STATE-02) ----------------

def squad_save(gameweek: int, source: str, payload: dict) -> None:
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO squads(gameweek, source, payload, saved_at) VALUES (?,?,?,?)",
            (gameweek, source, json.dumps(payload), time.time()),
        )


def squad_load(gameweek: int) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT payload FROM squads WHERE gameweek=?", (gameweek,)).fetchone()
    return None if row is None else json.loads(row[0])


def squad_latest() -> tuple[int, dict] | None:
    with _conn() as c:
        row = c.execute(
            "SELECT gameweek, payload FROM squads ORDER BY gameweek DESC LIMIT 1"
        ).fetchone()
    return None if row is None else (row[0], json.loads(row[1]))


# ---------------- kv (team id, weights) (DC-04) ----------------

def kv_get(key: str, default=None):
    with _conn() as c:
        row = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return default if row is None else json.loads(row[0])


def kv_set(key: str, value) -> None:
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO kv(key, value) VALUES (?,?)", (key, json.dumps(value)))
