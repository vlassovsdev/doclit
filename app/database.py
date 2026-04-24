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
            password    TEXT,
            google_id   TEXT    UNIQUE,
            avatar_url  TEXT,
            plan        TEXT    NOT NULL DEFAULT 'free',
            jobs_today  INTEGER NOT NULL DEFAULT 0,
            last_reset  TEXT    NOT NULL DEFAULT (date('now')),
            created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id              TEXT    PRIMARY KEY,
            user_id         INTEGER,
            anon_ip         TEXT,
            download_token  TEXT    UNIQUE,
            expires_at      TEXT,
            operation       TEXT    NOT NULL,
            status          TEXT    NOT NULL DEFAULT 'pending',
            input_file      TEXT    NOT NULL,
            output_file     TEXT,
            error           TEXT,
            created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT    NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE INDEX IF NOT EXISTS idx_jobs_user    ON jobs(user_id);
        CREATE INDEX IF NOT EXISTS idx_jobs_status  ON jobs(status);
        """)
        await db.commit()

    # Run migrations for existing databases
    await _migrate(DB_PATH)


async def _migrate(db_path: str):
    """Add new columns to existing tables if they don't exist."""
    async with aiosqlite.connect(db_path) as db:
        # Check existing columns in users table
        async with db.execute("PRAGMA table_info(users)") as cur:
            user_cols = {row[1] async for row in cur}

        for col, typedef in [
            ("google_id",  "TEXT"),
            ("avatar_url", "TEXT"),
        ]:
            if col not in user_cols:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col} {typedef}")

        # Check existing columns in jobs table
        async with db.execute("PRAGMA table_info(jobs)") as cur:
            job_cols = {row[1] async for row in cur}

        for col, typedef in [
            ("anon_ip",        "TEXT"),
            ("download_token", "TEXT"),
            ("expires_at",     "TEXT"),
        ]:
            if col not in job_cols:
                await db.execute(f"ALTER TABLE jobs ADD COLUMN {col} {typedef}")

        # Create indexes if not exists
        await db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_token ON jobs(download_token)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_expires ON jobs(expires_at)")

        await db.commit()
