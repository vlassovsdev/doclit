"""Secure download endpoint — files served only via valid download token."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
import aiosqlite, os, datetime

router = APIRouter()
DB_PATH = os.getenv("DB_PATH", "doclit.db")

CONTENT_TYPES = {
    ".pdf":  "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc":  "application/msword",
    ".txt":  "text/plain; charset=utf-8",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".zip":  "application/zip",
}


@router.get("/{token}")
async def download_by_token(token: str):
    """Download a processed file using its unique download token."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, status, output_file, expires_at FROM jobs WHERE download_token=?",
            (token,)
        ) as cur:
            job = await cur.fetchone()

    if not job:
        raise HTTPException(404, "Ссылка не найдена или недействительна")

    # Check expiry
    if job["expires_at"]:
        expires = datetime.datetime.fromisoformat(job["expires_at"])
        if datetime.datetime.utcnow() > expires:
            raise HTTPException(410, "Ссылка истекла. Документ был удалён.")

    # Check job status
    if job["status"] != "done":
        raise HTTPException(400, f"Файл ещё не готов. Статус: {job['status']}")

    output_file = job["output_file"]
    if not output_file or not os.path.exists(output_file):
        raise HTTPException(404, "Файл не найден на сервере")

    # Determine content type and filename
    ext = os.path.splitext(output_file)[1].lower()
    content_type = CONTENT_TYPES.get(ext, "application/octet-stream")

    # Generate a user-friendly filename
    filename = f"doclit_{job['id'][:8]}{ext}"

    return FileResponse(
        path=output_file,
        media_type=content_type,
        filename=filename,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, max-age=3600",
        }
    )
