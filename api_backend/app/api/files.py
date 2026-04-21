from fastapi import APIRouter, UploadFile, File, HTTPException, Header
import os, uuid, aiofiles

router = APIRouter()

ALLOWED_EXT  = {".pdf", ".doc", ".docx", ".png", ".jpg", ".jpeg"}
ANON_LIMIT   = 10 * 1024 * 1024   # 10 MB

@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    authorization: str = Header(default=""),
):
    ext = ("." + (file.filename or "").rsplit(".", 1)[-1]).lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"Формат не поддерживается: {ext}. Допустимы: {', '.join(ALLOWED_EXT)}")

    content = await file.read()
    if len(content) > ANON_LIMIT and not authorization:
        raise HTTPException(413, "Файл больше 10 МБ — войдите для увеличения лимита")

    os.makedirs("uploads", exist_ok=True)
    file_id   = str(uuid.uuid4())
    save_path = f"uploads/{file_id}{ext}"

    async with aiofiles.open(save_path, "wb") as f:
        await f.write(content)

    return {
        "file_id":   file_id,
        "filename":  file.filename,
        "size":      len(content),
        "extension": ext,
    }
