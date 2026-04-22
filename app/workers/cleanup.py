"""DocLit — очистка просроченных документов.

Удаляет:
 - Анонимные файлы старше 7 дней
 - Файлы зарегистрированных пользователей старше 90 дней

Запуск: python -m app.workers.cleanup
Cron:   0 3 * * * cd /var/www/html/doclit.vlasovs.tk/api_backend && venv/bin/python -m app.workers.cleanup
"""
import asyncio, os, aiosqlite, datetime

DB_PATH = os.getenv("DB_PATH", "doclit.db")


async def cleanup():
    now = datetime.datetime.utcnow().isoformat()
    deleted_files = 0
    deleted_jobs  = 0

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Find expired jobs
        async with db.execute(
            "SELECT id, input_file, output_file FROM jobs WHERE expires_at IS NOT NULL AND expires_at < ?",
            (now,)
        ) as cur:
            expired = await cur.fetchall()

        for job in expired:
            # Delete physical files
            for path in [job["input_file"], job["output_file"]]:
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                        deleted_files += 1
                    except OSError:
                        pass

            # Delete job record
            await db.execute("DELETE FROM jobs WHERE id=?", (job["id"],))
            deleted_jobs += 1

        await db.commit()

    print(f"[cleanup] {datetime.datetime.now().isoformat()} — "
          f"удалено заданий: {deleted_jobs}, файлов: {deleted_files}")


if __name__ == "__main__":
    asyncio.run(cleanup())
