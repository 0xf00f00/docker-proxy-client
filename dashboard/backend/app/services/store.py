"""Tiny SQLite-backed persistence so a dashboard restart doesn't wipe state.

Everything else stays in-memory by design. Writes are tiny and rare, so we open
a short-lived connection per operation rather than keeping one around.
"""

import secrets
import sqlite3
from pathlib import Path

from app.config import settings

_SECRET_KEY = "session_secret"


def _db_path() -> str:
    return str(Path(settings.state_dir) / "dashboard.db")


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
