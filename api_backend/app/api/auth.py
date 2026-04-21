from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext
from jose import jwt
from datetime import datetime, timedelta
import os

from app.database import get_db

router = APIRouter()
pwd    = CryptContext(schemes=["bcrypt"], deprecated="auto")
SECRET = os.getenv("JWT_SECRET", "change-me-in-production")
ALGO   = "HS256"

class RegisterIn(BaseModel):
    email: EmailStr
    password: str

class LoginIn(BaseModel):
    email: EmailStr
    password: str

def make_token(user_id: int, email: str) -> str:
    exp = datetime.utcnow() + timedelta(days=30)
    return jwt.encode({"sub": str(user_id), "email": email, "exp": exp}, SECRET, ALGO)

@router.post("/register")
async def register(data: RegisterIn, db=Depends(get_db)):
    async with db.execute("SELECT id FROM users WHERE email=?", (data.email,)) as cur:
        if await cur.fetchone():
            raise HTTPException(400, "Email уже зарегистрирован")
    hashed = pwd.hash(data.password)
    async with db.execute(
        "INSERT INTO users (email, password) VALUES (?,?) RETURNING id",
        (data.email, hashed)
    ) as cur:
        row = await cur.fetchone()
    await db.commit()
    return {"token": make_token(row["id"], data.email), "email": data.email, "plan": "free"}

@router.post("/login")
async def login(data: LoginIn, db=Depends(get_db)):
    async with db.execute(
        "SELECT id, password, plan FROM users WHERE email=?", (data.email,)
    ) as cur:
        user = await cur.fetchone()
    if not user or not pwd.verify(data.password, user["password"]):
        raise HTTPException(401, "Неверный email или пароль")
    return {"token": make_token(user["id"], data.email), "email": data.email, "plan": user["plan"]}

@router.get("/me")
async def me(authorization: str = "", db=Depends(get_db)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Нет токена")
    try:
        payload = jwt.decode(authorization[7:], SECRET, ALGO)
    except Exception:
        raise HTTPException(401, "Недействительный токен")
    async with db.execute(
        "SELECT id, email, plan, jobs_today FROM users WHERE id=?", (payload["sub"],)
    ) as cur:
        user = await cur.fetchone()
    if not user:
        raise HTTPException(404, "Пользователь не найден")
    return dict(user)
