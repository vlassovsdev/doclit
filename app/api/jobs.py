from fastapi import APIRouter, HTTPException, BackgroundTasks, Request
from pydantic import BaseModel
from typing import Optional
import uuid, glob, aiosqlite, os, datetime

from app.workers.processor import process_job
from app.api.auth import ALGO, SECRET, jwt

router = APIRouter()
DB_PATH = os.getenv("DB_PATH", "doclit.db")

# ── Per-plan daily limits ──────────────────────────────────
LIMITS = {
    "anonymous": 1,
    "free":      5,
    "pro":       1000,
}

VALID_OPS = {
    "pdf_to_docx", "pdf_to_png", "pdf_to_txt",
    "docx_to_pdf",
    "img_to_pdf", "img_to_txt",
    "pdf_delete_pages", "pdf_merge", "pdf_rotate",
}

# ── Expiry periods ─────────────────────────────────────────
ANON_EXPIRY_DAYS       = 7
REGISTERED_EXPIRY_DAYS = 90


async def check_limit(db: aiosqlite.Connection, user_id: Optional[int], ip: str):
    today = datetime.date.today().isoformat()

    if user_id:
        async with db.execute("SELECT jobs_today, last_reset, plan FROM users WHERE id=?", (user_id,)) as cur:
            row = await cur.fetchone()
            if row:
                jobs_today, last_reset, plan = row["jobs_today"], row["last_reset"], row["plan"]
                if last_reset != today:
                    await db.execute("UPDATE users SET jobs_today=0, last_reset=? WHERE id=?", (today, user_id))
                    jobs_today = 0
                limit = LIMITS.get(plan, LIMITS["free"])
                if jobs_today >= limit:
                    raise HTTPException(429, f"Лимит исчерпан: {limit} документов в день. Попробуйте завтра.")
                return True
    else:
        limit = LIMITS["anonymous"]
        async with db.execute("SELECT jobs_today, last_reset FROM anonymous_usage WHERE ip=?", (ip,)) as cur:
            row = await cur.fetchone()
            if row:
                jobs_today, last_reset = row["jobs_today"], row["last_reset"]
                if last_reset != today:
                    await db.execute("UPDATE anonymous_usage SET jobs_today=0, last_reset=? WHERE ip=?", (today, ip))
                    jobs_today = 0
                if jobs_today >= limit:
                    raise HTTPException(429, f"Лимит исчерпан: {limit} документ в день без регистрации. Зарегистрируйтесь для 5 документов/день.")
            else:
                await db.execute("INSERT INTO anonymous_usage (ip, jobs_today, last_reset) VALUES (?, 0, ?)", (ip, today))
    return True

async def increment_limit(db: aiosqlite.Connection, user_id: Optional[int], ip: str):
    if user_id:
        await db.execute("UPDATE users SET jobs_today = jobs_today + 1 WHERE id=?", (user_id,))
    else:
        await db.execute("UPDATE anonymous_usage SET jobs_today = jobs_today + 1 WHERE ip=?", (ip,))
    await db.commit()

async def get_user_id(request: Request) -> Optional[int]:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    try:
        payload = jwt.decode(auth_header[7:], SECRET, ALGO)
        return int(payload.get("sub"))
    except:
        return None

async def get_user_plan(db: aiosqlite.Connection, user_id: int) -> str:
    async with db.execute("SELECT plan FROM users WHERE id=?", (user_id,)) as cur:
        row = await cur.fetchone()
        return row["plan"] if row else "free"

class JobCreate(BaseModel):
    file_id: str
    operation: str
    options: Optional[dict] = {}

@router.post("/create")
async def create_job(data: JobCreate, request: Request, background_tasks: BackgroundTasks):
    if data.operation not in VALID_OPS:
        raise HTTPException(400, f"Неизвестная операция: {data.operation}")

    matches = glob.glob(f"uploads/{data.file_id}.*")
    if not matches:
        raise HTTPException(404, "Файл не найден. Сначала загрузите файл через /api/files/upload")

    input_path     = matches[0]
    job_id         = str(uuid.uuid4())
    download_token = str(uuid.uuid4())
    user_id        = await get_user_id(request)
    ip             = request.client.host

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await check_limit(db, user_id, ip)

        # Calculate expiry
        if user_id:
            expires_at = (datetime.datetime.utcnow() + datetime.timedelta(days=REGISTERED_EXPIRY_DAYS)).isoformat()
        else:
            expires_at = (datetime.datetime.utcnow() + datetime.timedelta(days=ANON_EXPIRY_DAYS)).isoformat()

        await db.execute(
            """INSERT INTO jobs (id, user_id, anon_ip, download_token, expires_at, operation, status, input_file)
               VALUES (?,?,?,?,?,?,?,?)""",
            (job_id, user_id, ip if not user_id else None, download_token, expires_at,
             data.operation, "pending", input_path)
        )
        await increment_limit(db, user_id, ip)

    background_tasks.add_task(process_job, job_id, input_path, data.operation, data.options or {})
    return {"job_id": job_id, "status": "pending", "download_token": download_token}

@router.get("/{job_id}")
async def get_job(job_id: str, request: Request):
    user_id = await get_user_id(request)
    ip      = request.client.host

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, user_id, anon_ip, download_token, expires_at, operation, status, output_file, error, created_at FROM jobs WHERE id=?",
            (job_id,)
        ) as cur:
            job = await cur.fetchone()

    if not job:
        raise HTTPException(404, "Задание не найдено")

    # Access check: owner (user_id match) or anonymous (same IP)
    if job["user_id"]:
        if user_id != job["user_id"]:
            raise HTTPException(403, "Нет доступа к этому заданию")
    else:
        if job["anon_ip"] != ip and not user_id:
            raise HTTPException(403, "Нет доступа к этому заданию")

    result = {
        "id":        job["id"],
        "operation": job["operation"],
        "status":    job["status"],
        "error":     job["error"],
        "created_at": job["created_at"],
        "download_token": job["download_token"],
        "expires_at": job["expires_at"],
    }

    if job["status"] == "done" and job["output_file"]:
        result["download_url"] = f"/api/download/{job['download_token']}"

    return result

@router.get("/")
async def list_jobs(request: Request, limit: int = 20):
    user_id = await get_user_id(request)
    ip      = request.client.host

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        if user_id:
            async with db.execute(
                "SELECT id, operation, status, output_file, download_token, created_at FROM jobs WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit)
            ) as cur:
                jobs = await cur.fetchall()
        else:
            async with db.execute(
                "SELECT id, operation, status, output_file, download_token, created_at FROM jobs WHERE anon_ip=? AND user_id IS NULL ORDER BY created_at DESC LIMIT ?",
                (ip, limit)
            ) as cur:
                jobs = await cur.fetchall()

    result = []
    for j in jobs:
        row = {
            "id":        j["id"],
            "operation": j["operation"],
            "status":    j["status"],
            "created_at": j["created_at"],
        }
        if j["status"] == "done" and j["output_file"]:
            row["download_url"] = f"/api/download/{j['download_token']}"
        result.append(row)
    return result
