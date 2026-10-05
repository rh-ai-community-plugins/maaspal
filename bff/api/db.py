import contextlib
import os
from pathlib import Path

import aiosqlite


def get_db_path() -> str:
    return os.environ.get("DB_PATH", "/data/maaspal.db")


async def init_db() -> None:
    path = get_db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id               TEXT PRIMARY KEY,
                scenario         TEXT NOT NULL,
                status           TEXT NOT NULL DEFAULT 'PENDING',
                created_at       TEXT NOT NULL,
                updated_at       TEXT NOT NULL,
                config_overrides TEXT,
                duration_ms      REAL,
                auto_cleanup     INTEGER NOT NULL DEFAULT 1,
                cleanup_status   TEXT NOT NULL DEFAULT 'pending',
                cleanup_error    TEXT
            )
            """
        )
        # Migration: add config_overrides column to existing databases
        with contextlib.suppress(Exception):
            await db.execute("ALTER TABLE runs ADD COLUMN config_overrides TEXT")
        # Migration: add duration_ms column to existing databases
        with contextlib.suppress(Exception):
            await db.execute("ALTER TABLE runs ADD COLUMN duration_ms REAL")
        # Migration: add auto-cleanup toggle/status columns to existing databases
        with contextlib.suppress(Exception):
            await db.execute("ALTER TABLE runs ADD COLUMN auto_cleanup INTEGER NOT NULL DEFAULT 1")
        with contextlib.suppress(Exception):
            await db.execute("ALTER TABLE runs ADD COLUMN cleanup_status TEXT NOT NULL DEFAULT 'pending'")
        with contextlib.suppress(Exception):
            await db.execute("ALTER TABLE runs ADD COLUMN cleanup_error TEXT")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS task_results (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id      TEXT NOT NULL REFERENCES runs(id),
                task_name   TEXT NOT NULL,
                status      TEXT NOT NULL,
                duration_ms REAL,
                error       TEXT
            )
            """
        )
        await db.commit()
