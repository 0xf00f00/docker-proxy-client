"""Tiny SQLite-backed persistence so a dashboard restart doesn't wipe state.

Everything else stays in-memory by design. Writes are tiny and rare, so we open
a short-lived connection per operation rather than keeping one around.
"""

import contextlib
import secrets
import sqlite3
from pathlib import Path

from app.config import settings

_SECRET_KEY = "session_secret"


def _db_path() -> str:
    return str(Path(settings.state_dir) / "dashboard.db")


def _usage_db_path() -> Path:
    # Own file so opt-out can delete it outright (no residual pages in the main db).
    return Path(settings.state_dir) / "usage.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init() -> None:
    Path(settings.state_dir).mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS connectivity_cache ("
            "service TEXT PRIMARY KEY, result_json TEXT NOT NULL, tested_at TEXT NOT NULL)"
        )


def get_or_create_session_secret() -> bytes:
    """Return the persistent 32-byte cookie-signing secret, creating it once."""
    with _connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (_SECRET_KEY,)).fetchone()
        if row is not None:
            return bytes.fromhex(row[0])
        secret = secrets.token_bytes(32)
        conn.execute("INSERT INTO meta (key, value) VALUES (?, ?)", (_SECRET_KEY, secret.hex()))
        return secret


def upsert_connectivity(service: str, result_json: str, tested_at: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO connectivity_cache (service, result_json, tested_at) VALUES (?, ?, ?) "
            "ON CONFLICT(service) DO UPDATE SET result_json = excluded.result_json, tested_at = excluded.tested_at",
            (service, result_json, tested_at),
        )


def load_connectivity() -> list[tuple[str, str, str]]:
    with _connect() as conn:
        return conn.execute("SELECT service, result_json, tested_at FROM connectivity_cache").fetchall()


# ---------- Per-domain usage history (separate, deletable db) ----------


def _usage_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_usage_db_path()))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_usage() -> None:
    Path(settings.state_dir).mkdir(parents=True, exist_ok=True)
    with _usage_connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS usage_hourly ("
            "domain TEXT NOT NULL, hour_ts INTEGER NOT NULL, "
            "down INTEGER NOT NULL DEFAULT 0, up INTEGER NOT NULL DEFAULT 0, "
            "PRIMARY KEY(domain, hour_ts))"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_hour ON usage_hourly(hour_ts)")


def upsert_usage(rows: list[tuple[str, int, int, int]]) -> None:
    """Add (domain, hour_ts, down, up) byte deltas into the rolling tally."""
    if not rows:
        return
    with _usage_connect() as conn:
        conn.executemany(
            "INSERT INTO usage_hourly (domain, hour_ts, down, up) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(domain, hour_ts) DO UPDATE SET "
            "down = down + excluded.down, up = up + excluded.up",
            rows,
        )


def query_usage_top(since_ts: int, until_ts: int, limit: int) -> list[tuple[str, int, int]]:
    """Top domains by total bytes within [since_ts, until_ts): (domain, down, up)."""
    with _usage_connect() as conn:
        return conn.execute(
            "SELECT domain, SUM(down) AS d, SUM(up) AS u FROM usage_hourly "
            "WHERE hour_ts >= ? AND hour_ts < ? GROUP BY domain ORDER BY d + u DESC LIMIT ?",
            (since_ts, until_ts, limit),
        ).fetchall()


def query_usage_series(since_ts: int, until_ts: int) -> list[tuple[int, int]]:
    """Per-hour combined-byte totals in [since_ts, until_ts): (hour_ts, down+up)."""
    with _usage_connect() as conn:
        return conn.execute(
            "SELECT hour_ts, SUM(down + up) FROM usage_hourly "
            "WHERE hour_ts >= ? AND hour_ts < ? GROUP BY hour_ts ORDER BY hour_ts",
            (since_ts, until_ts),
        ).fetchall()


def query_usage_total(since_ts: int, until_ts: int) -> tuple[int, int]:
    """Grand (down, up) totals across all domains in [since_ts, until_ts)."""
    with _usage_connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(down), 0), COALESCE(SUM(up), 0) FROM usage_hourly "
            "WHERE hour_ts >= ? AND hour_ts < ?",
            (since_ts, until_ts),
        ).fetchone()
    return int(row[0]), int(row[1])


def prune_usage(before_ts: int) -> None:
    with _usage_connect() as conn:
        conn.execute("DELETE FROM usage_hourly WHERE hour_ts < ?", (before_ts,))


def wipe_usage() -> None:
    """Delete the whole usage db (file + WAL/SHM sidecars). Used on opt-out."""
    base = _usage_db_path()
    for suffix in ("", "-wal", "-shm"):
        with contextlib.suppress(FileNotFoundError):
            base.with_name(base.name + suffix).unlink()
