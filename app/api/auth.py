from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext
from jose import jwt
from datetime import datetime, timedelta
import os, httpx

from app.database import get_db

router = APIRouter()
pwd    = CryptContext(schemes=["bcrypt"], deprecated="auto")
SECRET = os.getenv("JWT_SECRET", "change-me-in-production")
ALGO   = "HS256"

GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")

class RegisterIn(BaseModel):
    email: EmailStr
    password: str

class LoginIn(BaseModel):
    email: EmailStr
    password: str

class GoogleAuthIn(BaseModel):
    credential: str  # Google ID token from frontend

def make_token(user_id: int, email: str, plan: str = "free") -> str:
    exp = datetime.utcnow() + timedelta(days=30)
    return jwt.encode({"sub": str(user_id), "email": email, "plan": plan, "exp": exp}, SECRET, ALGO)

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
    if not user:
        raise HTTPException(401, "Неверный email или пароль")
    if not user["password"]:
        raise HTTPException(401, "Этот аккаунт использует вход через Google. Нажмите «Войти через Google».")
    if not pwd.verify(data.password, user["password"]):
        raise HTTPException(401, "Неверный email или пароль")
    return {"token": make_token(user["id"], data.email, user["plan"]), "email": data.email, "plan": user["plan"]}

@router.post("/google")
async def google_auth(data: GoogleAuthIn, db=Depends(get_db)):
    """Authenticate with Google ID token (from Google Identity Services)."""
    # Verify the Google ID token
    google_user = await _verify_google_token(data.credential)
    if not google_user:
        raise HTTPException(401, "Не удалось верифицировать Google аккаунт")

    email     = google_user["email"]
    google_id = google_user["sub"]
    avatar    = google_user.get("picture", "")
    name      = google_user.get("name", "")

    # Check if user exists by google_id
    async with db.execute(
        "SELECT id, email, plan FROM users WHERE google_id=?", (google_id,)
    ) as cur:
        existing = await cur.fetchone()

    if existing:
        # Existing Google user — just login
        return {
            "token": make_token(existing["id"], existing["email"], existing["plan"]),
            "email": existing["email"],
            "plan":  existing["plan"],
            "avatar": avatar,
        }

    # Check if user exists by email (registered via email/password)
    async with db.execute(
        "SELECT id, email, plan FROM users WHERE email=?", (email,)
    ) as cur:
        email_user = await cur.fetchone()

    if email_user:
        # Link Google account to existing email user
        await db.execute(
            "UPDATE users SET google_id=?, avatar_url=? WHERE id=?",
            (google_id, avatar, email_user["id"])
        )
        await db.commit()
        return {
            "token": make_token(email_user["id"], email_user["email"], email_user["plan"]),
            "email": email_user["email"],
            "plan":  email_user["plan"],
            "avatar": avatar,
        }

    # New user — create account
    async with db.execute(
        "INSERT INTO users (email, google_id, avatar_url) VALUES (?,?,?) RETURNING id",
        (email, google_id, avatar)
    ) as cur:
        row = await cur.fetchone()
    await db.commit()

    return {
        "token": make_token(row["id"], email),
        "email": email,
        "plan":  "free",
        "avatar": avatar,
    }


async def _verify_google_token(id_token: str) -> dict | None:
    """Verify Google ID token by calling Google's tokeninfo endpoint."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://oauth2.googleapis.com/tokeninfo",
                params={"id_token": id_token},
                timeout=10,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()

            # Verify the token was meant for our app
            if data.get("aud") != GOOGLE_CLIENT_ID:
                return None

            # Check email is verified
            if data.get("email_verified") != "true":
                return None

            return data
    except Exception:
        return None


@router.get("/me")
async def me(authorization: str = "", db=Depends(get_db)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Нет токена")
    try:
        payload = jwt.decode(authorization[7:], SECRET, ALGO)
    except Exception:
        raise HTTPException(401, "Недействительный токен")
    async with db.execute(
        "SELECT id, email, plan, jobs_today, avatar_url FROM users WHERE id=?", (payload["sub"],)
    ) as cur:
        user = await cur.fetchone()
    if not user:
        raise HTTPException(404, "Пользователь не найден")
    result = dict(user)
    result["avatar"] = result.pop("avatar_url", None)
    return result
