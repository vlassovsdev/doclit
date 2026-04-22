from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import os

from app.api import files, jobs, auth, download
from app.database import init_db

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield

app = FastAPI(title="DocLit API", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router,     prefix="/api/auth",     tags=["auth"])
app.include_router(files.router,    prefix="/api/files",    tags=["files"])
app.include_router(jobs.router,     prefix="/api/jobs",     tags=["jobs"])
app.include_router(download.router, prefix="/api/download", tags=["download"])

# NOTE: /outputs is no longer mounted as StaticFiles.
# All downloads go through /api/download/{token} for access control.

@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.2.0"}
