"""SQLite persistence for ping history.

Schema is intentionally tiny: one row per (server, timestamp). `rtt_ms`
is NULL for lost packets so the chart can render gaps.

SQLite connections are per-thread. Each caller (worker thread, UI thread)
creates its own connection via `connect()`.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Iterable, Optional

from paths import app_dir

# One shared DB file next to the executable (or main.py in dev). Easy to
# inspect / delete — SQLite CLI, DB Browser, etc. all work on it directly.
DB_PATH = app_dir() / "history.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pings (
    ts       REAL NOT NULL,   -- unix seconds
    server   TEXT NOT NULL,
    host     TEXT NOT NULL,
    rtt_ms   REAL             -- NULL on loss
);
CREATE INDEX IF NOT EXISTS idx_pings_ts ON pings(ts);
CREATE INDEX IF NOT EXISTS idx_pings_server_ts ON pings(server, ts);

-- Generic time-series for system metrics (CPU %, memory %, net speed, etc.)
-- `name` values are defined in system_worker.METRIC_NAMES so the chart's
-- dropdown + the DB stay in sync.
CREATE TABLE IF NOT EXISTS metrics (
    ts     REAL NOT NULL,
    name   TEXT NOT NULL,
    value  REAL
);
CREATE INDEX IF NOT EXISTS idx_metrics_ts ON metrics(ts);
CREATE INDEX IF NOT EXISTS idx_metrics_name_ts ON metrics(name, ts);
"""


def connect() -> sqlite3.Connection:
    """Open a connection tuned for low-overhead concurrent writes + reads."""
    conn = sqlite3.connect(str(DB_PATH), timeout=5.0, isolation_level=None)
    # WAL so the chart window can read while the worker writes.
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")  # fine for local telemetry
    conn.execute("PRAGMA temp_store=MEMORY;")
    return conn


def init_db(retention_days: int = 7) -> None:
    """Create tables if missing and prune rows older than `retention_days`."""
    conn = connect()
    try:
        conn.executescript(_SCHEMA)
        cutoff = time.time() - retention_days * 86400
        conn.execute("DELETE FROM pings WHERE ts < ?;", (cutoff,))
        conn.execute("DELETE FROM metrics WHERE ts < ?;", (cutoff,))
        conn.execute("VACUUM;")
    finally:
        conn.close()


def insert_round(
    conn: sqlite3.Connection,
    rows: Iterable[tuple[float, str, str, Optional[float]]],
) -> None:
    """Batch-insert one ping round. `rows` = (ts, server, host, rtt_ms)."""
    conn.executemany(
        "INSERT INTO pings (ts, server, host, rtt_ms) VALUES (?, ?, ?, ?);",
        list(rows),
    )


def fetch_range(
    conn: sqlite3.Connection,
    since_ts: float,
    server: Optional[str] = None,
) -> list[tuple[float, str, Optional[float]]]:
    """Return (ts, server, rtt_ms) rows newer than `since_ts`, oldest first."""
    if server is None:
        cur = conn.execute(
            "SELECT ts, server, rtt_ms FROM pings "
            "WHERE ts >= ? ORDER BY ts ASC;",
            (since_ts,),
        )
    else:
        cur = conn.execute(
            "SELECT ts, server, rtt_ms FROM pings "
            "WHERE ts >= ? AND server = ? ORDER BY ts ASC;",
            (since_ts, server),
        )
    return cur.fetchall()


def distinct_servers(conn: sqlite3.Connection) -> list[str]:
    cur = conn.execute("SELECT DISTINCT server FROM pings ORDER BY server;")
    return [r[0] for r in cur.fetchall()]


def earliest_ts(conn: sqlite3.Connection) -> Optional[float]:
    cur = conn.execute("SELECT MIN(ts) FROM pings;")
    row = cur.fetchone()
    return row[0] if row and row[0] is not None else None


# --- metrics table helpers ---------------------------------------------------

def insert_metrics(
    conn: sqlite3.Connection,
    rows: Iterable[tuple[float, str, Optional[float]]],
) -> None:
    """Batch-insert (ts, name, value) tuples into `metrics`."""
    conn.executemany(
        "INSERT INTO metrics (ts, name, value) VALUES (?, ?, ?);",
        list(rows),
    )


def fetch_metric(
    conn: sqlite3.Connection,
    name: str,
    since_ts: float,
) -> list[tuple[float, Optional[float]]]:
    """Return (ts, value) rows for a metric newer than `since_ts`."""
    cur = conn.execute(
        "SELECT ts, value FROM metrics WHERE name = ? AND ts >= ? ORDER BY ts ASC;",
        (name, since_ts),
    )
    return cur.fetchall()


def earliest_metric_ts(conn: sqlite3.Connection, name: str) -> Optional[float]:
    cur = conn.execute("SELECT MIN(ts) FROM metrics WHERE name = ?;", (name,))
    row = cur.fetchone()
    return row[0] if row and row[0] is not None else None
