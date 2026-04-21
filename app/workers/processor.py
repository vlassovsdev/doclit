"""DocLit — обработчик заданий (все операции с документами)."""
import os, uuid, asyncio, subprocess, shutil
import aiosqlite

DB_PATH = os.getenv("DB_PATH", "doclit.db")

async def _set_status(job_id: str, status: str, output: str = None, error: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE jobs SET status=?, output_file=?, error=?, updated_at=datetime('now') WHERE id=?",
            (status, output, error, job_id)
        )
        await db.commit()

async def process_job(job_id: str, input_path: str, operation: str, options: dict):
    await _set_status(job_id, "processing")
    try:
        out_path = await asyncio.get_event_loop().run_in_executor(
            None, _sync_dispatch, input_path, operation, options
        )
        await _set_status(job_id, "done", output=out_path)
    except Exception as e:
        await _set_status(job_id, "error", error=str(e))

def _sync_dispatch(input_path: str, op: str, opts: dict) -> str:
    os.makedirs("outputs", exist_ok=True)
    out_id = str(uuid.uuid4())
    dispatch = {
        "pdf_to_docx":       lambda: pdf_to_docx(input_path, out_id),
        "pdf_to_png":        lambda: pdf_to_png(input_path, out_id),
        "pdf_to_txt":        lambda: pdf_to_txt(input_path, out_id),
        "docx_to_pdf":       lambda: docx_to_pdf(input_path, out_id),
        "img_to_pdf":        lambda: img_to_pdf(input_path, out_id),
        "img_to_txt":        lambda: img_to_txt(input_path, out_id, opts),
        "pdf_delete_pages":  lambda: pdf_delete_pages(input_path, out_id, opts),
        "pdf_merge":         lambda: pdf_merge(input_path, out_id, opts),
        "pdf_rotate":        lambda: pdf_rotate(input_path, out_id, opts),
    }
    if op not in dispatch:
        raise ValueError(f"Неизвестная операция: {op}")
    return dispatch[op]()

# ── PDF → DOCX ───────────────────────────────────────────────
def pdf_to_docx(src: str, out_id: str) -> str:
    try:
        from pdf2docx import Converter
        out = f"outputs/{out_id}.docx"
        cv = Converter(src)
        cv.convert(out)
        cv.close()
        return out
    except Exception:
        return _libreoffice(src, out_id, "docx")

# ── DOCX → PDF ───────────────────────────────────────────────
def docx_to_pdf(src: str, out_id: str) -> str:
    return _libreoffice(src, out_id, "pdf")

def _libreoffice(src: str, out_id: str, fmt: str) -> str:
    out_dir = "outputs"
    r = subprocess.run(
        ["libreoffice", "--headless", "--convert-to", fmt, "--outdir", out_dir, src],
        capture_output=True, text=True, timeout=120
    )
    if r.returncode != 0:
        raise RuntimeError(f"LibreOffice: {r.stderr.strip()}")
    base  = os.path.splitext(os.path.basename(src))[0]
    lo_out = f"{out_dir}/{base}.{fmt}"
    final  = f"{out_dir}/{out_id}.{fmt}"
    shutil.move(lo_out, final)
    return final

# ── PDF → PNG ────────────────────────────────────────────────
def pdf_to_png(src: str, out_id: str) -> str:
    import fitz, zipfile
    doc  = fitz.open(src)
    pngs = []
    for i, page in enumerate(doc):
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        p   = f"outputs/{out_id}_p{i+1}.png"
        pix.save(p)
        pngs.append(p)
    doc.close()
    if len(pngs) == 1:
        return pngs[0]
    zip_path = f"outputs/{out_id}.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for p in pngs:
            zf.write(p, os.path.basename(p))
            os.remove(p)
    return zip_path

# ── PDF → TXT ────────────────────────────────────────────────
def pdf_to_txt(src: str, out_id: str) -> str:
    import fitz
    doc  = fitz.open(src)
    text = "\n\n".join(p.get_text() for p in doc)
    doc.close()
    if len(text.strip()) < 50:
        return _ocr_pdf(src, out_id)
    out = f"outputs/{out_id}.txt"
    open(out, "w", encoding="utf-8").write(text)
    return out

def _ocr_pdf(src: str, out_id: str) -> str:
    import fitz, pytesseract
    from PIL import Image
    import io
    doc   = fitz.open(src)
    texts = []
    for page in doc:
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        texts.append(pytesseract.image_to_string(img, lang="rus+eng+kaz"))
    doc.close()
    out = f"outputs/{out_id}.txt"
    open(out, "w", encoding="utf-8").write("\n\n--- страница ---\n\n".join(texts))
    return out

# ── IMAGE → PDF ──────────────────────────────────────────────
def img_to_pdf(src: str, out_id: str) -> str:
    import fitz
    doc  = fitz.open()
    img  = fitz.open(src)
    rect = img[0].rect if hasattr(img, '__iter__') else fitz.Rect(0, 0, 595, 842)
    try:
        from PIL import Image as PILImage
        w, h = PILImage.open(src).size
        rect = fitz.Rect(0, 0, w * 0.75, h * 0.75)
    except Exception:
        pass
    page = doc.new_page(width=rect.width, height=rect.height)
    page.insert_image(page.rect, filename=src)
    out  = f"outputs/{out_id}.pdf"
    doc.save(out)
    doc.close()
    return out

# ── IMAGE → TXT (OCR) ────────────────────────────────────────
def img_to_txt(src: str, out_id: str, opts: dict) -> str:
    import pytesseract
    from PIL import Image
    lang = opts.get("lang", "rus+eng+kaz")
    text = pytesseract.image_to_string(Image.open(src), lang=lang)
    out  = f"outputs/{out_id}.txt"
    open(out, "w", encoding="utf-8").write(text)
    return out

# ── PDF — УДАЛИТЬ СТРАНИЦЫ ───────────────────────────────────
def pdf_delete_pages(src: str, out_id: str, opts: dict) -> str:
    import fitz
    pages = opts.get("pages", [])
    if not pages:
        raise ValueError("Укажите pages: [1,3] — номера страниц для удаления")
    doc     = fitz.open(src)
    indices = sorted([p - 1 for p in pages if 1 <= p <= doc.page_count], reverse=True)
    for i in indices:
        doc.delete_page(i)
    out = f"outputs/{out_id}.pdf"
    doc.save(out)
    doc.close()
    return out

# ── PDF — ОБЪЕДИНИТЬ ─────────────────────────────────────────
def pdf_merge(src: str, out_id: str, opts: dict) -> str:
    import fitz
    merged = fitz.open()
    for f in [src] + opts.get("files", []):
        doc = fitz.open(f)
        merged.insert_pdf(doc)
        doc.close()
    out = f"outputs/{out_id}.pdf"
    merged.save(out)
    merged.close()
    return out

# ── PDF — ПОВЕРНУТЬ ──────────────────────────────────────────
def pdf_rotate(src: str, out_id: str, opts: dict) -> str:
    import fitz
    angle = opts.get("angle", 90)
    pages = opts.get("pages", None)
    doc   = fitz.open(src)
    for i, page in enumerate(doc):
        if pages is None or (i + 1) in pages:
            page.set_rotation(page.rotation + angle)
    out = f"outputs/{out_id}.pdf"
    doc.save(out)
    doc.close()
    return out
