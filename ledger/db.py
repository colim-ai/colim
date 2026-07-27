"""Ledger accessors. The ledger is the spine; nothing else stores a number."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

LEDGER_DIR = Path(__file__).resolve().parent
DB_PATH = LEDGER_DIR / "colim.sqlite"
SCHEMA_PATH = LEDGER_DIR / "schema.sql"


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# Columns added to `packages` after the first ledgers were built. CREATE TABLE
# IF NOT EXISTS will not add them to an existing table, so they are applied
# additively -- the alternative is dropping a ledger that took API calls to fill.
_ADDED_COLUMNS = {
    "packages": {
        "first_error": "TEXT",
        "error_file_count": "INTEGER",
        "error_files": "TEXT",
        "infra_only": "INTEGER",
    },
}


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    """Open the ledger, creating/migrating schema. Safe to call repeatedly."""
    # Builds run for hours while other stages query the ledger, so a writer must
    # wait for a lock rather than dying on it -- losing an hour-long build to a
    # 50ms lock contention is not acceptable. WAL (set in schema.sql) plus a
    # generous busy timeout makes reader/writer overlap safe.
    conn = sqlite3.connect(path, timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 60000")
    conn.executescript(SCHEMA_PATH.read_text())

    for table, cols in _ADDED_COLUMNS.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    conn.commit()
    return conn


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value, updated_at) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, str(value), now()),
    )


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def upsert_package(conn: sqlite3.Connection, row: dict) -> None:
    """Insert or update one package row.

    Idempotent by pkg_key so reruns of the census overwrite census-owned columns
    without touching repair-pipeline columns written by later stages.
    """
    row = {**row, "updated_at": now()}
    cols = ", ".join(row)
    placeholders = ", ".join(f":{c}" for c in row)
    updates = ", ".join(f"{c}=excluded.{c}" for c in row if c != "pkg_key")
    conn.execute(
        f"INSERT INTO packages ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT(pkg_key) DO UPDATE SET {updates}",
        row,
    )


def replace_observations(conn: sqlite3.Connection, pkg_key: str, obs: list[dict]) -> None:
    conn.execute("DELETE FROM build_observations WHERE pkg_key=?", (pkg_key,))
    conn.executemany(
        "INSERT OR REPLACE INTO build_observations"
        "(pkg_key, toolchain, revision, built, run_at, url) "
        "VALUES(:pkg_key, :toolchain, :revision, :built, :run_at, :url)",
        [{**o, "pkg_key": pkg_key} for o in obs],
    )
