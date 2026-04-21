import aiosqlite
import os

DB_PATH = os.getenv("DB_PATH", "doclit.db")

async def get_db():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        yield db

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS anonymous_usage (
            ip          TEXT    PRIMARY KEY,
            jobs_today  INTEGER NOT NULL DEFAULT 0,
            last_reset  TEXT    NOT NULL DEFAULT (date('now'))
        );

        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            email       TEXT    UNIQUE NOT NULL,
            password    TEXT    NOT NULL,
            plan        TEXT    NOT NULL DEFAULT 'free',
            jobs_today  INTEGER NOT NULL DEFAULT 0,
            last_reset  TEXT    NOT NULL DEFAULT (date('now')),
            created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id          TEXT    PRIMARY KEY,
            user_id     INTEGER,
            operation   TEXT    NOT NULL,
            status      TEXT    NOT NULL DEFAULT 'pending',
            input_file  TEXT    NOT NULL,
            output_file TEXT,
            error       TEXT,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE INDEX IF NOT EXISTS idx_jobs_user   ON jobs(user_id);
        CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
        """)
        await db.commit()
