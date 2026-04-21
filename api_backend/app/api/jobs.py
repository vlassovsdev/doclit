from fastapi import APIRouter, HTTPException, BackgroundTasks, Request
from pydantic import BaseModel
from typing import Optional
import uuid, glob, aiosqlite, os, datetime

from app.workers.processor import process_job
from app.api.auth import ALGO, SECRET, jwt

router = APIRouter()
DB_PATH = os.getenv("DB_PATH", "doclit.db")
DAILY_LIMIT = 5

VALID_OPS = {
    "pdf_to_docx", "pdf_to_png", "pdf_to_txt",
    "docx_to_pdf",
    "img_to_pdf", "img_to_txt",
    "pdf_delete_pages", "pdf_merge", "pdf_rotate",
}

async def check_limit(db: aiosqlite.Connection, user_id: Optional[int], ip: str):
    today = datetime.date.today().isoformat()
    
    if user_id:
        async with db.execute("SELECT jobs_today, last_reset FROM users WHERE id=?", (user_id,)) as cur:
            row = await cur.fetchone()
            if row:
                jobs_today, last_reset = row["jobs_today"], row["last_reset"]
                if last_reset != today:
                    await db.execute("UPDATE users SET jobs_today=0, last_reset=? WHERE id=?", (today, user_id))
                    jobs_today = 0
                if jobs_today >= DAILY_LIMIT:
                    raise HTTPException(429, f"Лимит исчерпан: {DAILY_LIMIT} документов в день. Попробуйте завтра.")
                return True
    else:
        async with db.execute("SELECT jobs_today, last_reset FROM anonymous_usage WHERE ip=?", (ip,)) as cur:
            row = await cur.fetchone()
            if row:
                jobs_today, last_reset = row["jobs_today"], row["last_reset"]
                if last_reset != today:
                    await db.execute("UPDATE anonymous_usage SET jobs_today=0, last_reset=? WHERE ip=?", (today, ip))
                    jobs_today = 0
                if jobs_today >= DAILY_LIMIT:
                    raise HTTPException(429, f"Лимит исчерпан: {DAILY_LIMIT} документов в день. Зарегистрируйтесь для увеличения лимитов.")
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

    input_path = matches[0]
    job_id     = str(uuid.uuid4())
    user_id    = await get_user_id(request)
    ip         = request.client.host

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await check_limit(db, user_id, ip)
        
        await db.execute(
            "INSERT INTO jobs (id, user_id, operation, status, input_file) VALUES (?,?,?,?,?)",
            (job_id, user_id, data.operation, "pending", input_path)
        )
        await increment_limit(db, user_id, ip)

    background_tasks.add_task(process_job, job_id, input_path, data.operation, data.options or {})
    return {"job_id": job_id, "status": "pending"}

@router.get("/{job_id}")
async def get_job(job_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, operation, status, output_file, error, created_at FROM jobs WHERE id=?",
            (job_id,)
        ) as cur:
            job = await cur.fetchone()

    if not job:
        raise HTTPException(404, "Задание не найдено")

    result = dict(job)
    if job["status"] == "done" and job["output_file"]:
        result["download_url"] = f"/outputs/{os.path.basename(job['output_file'])}"
    return result

@router.get("/")
async def list_jobs(limit: int = 20):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, operation, status, output_file, created_at FROM jobs ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ) as cur:
            jobs = await cur.fetchall()
    result = []
    for j in jobs:
        row = dict(j)
        if j["status"] == "done" and j["output_file"]:
            row["download_url"] = f"/outputs/{os.path.basename(j['output_file'])}"
        result.append(row)
    return result
