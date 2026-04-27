from fastapi import FastAPI, APIRouter, HTTPException, Depends, Header
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
import random
import hashlib
import jwt as pyjwt
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional
import uuid
from datetime import datetime, timezone, timedelta


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ.get("DB_NAME", "eliteplaybd")]

JWT_SECRET = os.environ.get("JWT_SECRET", "elite-play-bd-secret-2026")
JWT_ALGO = "HS256"

app = FastAPI()
api_router = APIRouter(prefix="/api")


# ---------------- Helpers ----------------
def hash_password(password: str) -> str:
    return hashlib.sha256(f"epbd::{password}".encode()).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(days=30),
    }
    return pyjwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


async def get_current_user(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    token = authorization.split(" ", 1)[1]
    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        user_id = payload.get("sub")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = await db.users.find_one({"id": user_id}, {"_id": 0, "password": 0})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


# ---------------- Models ----------------
class RegisterIn(BaseModel):
    name: str
    phone: str
    password: str


class LoginIn(BaseModel):
    phone: str
    password: str


class AuthOut(BaseModel):
    token: str
    user: dict


class DepositIn(BaseModel):
    amount: float
    method: str  # bKash / Nagad / Rocket


class WithdrawIn(BaseModel):
    amount: float
    method: str
    account: str


class JoinMatchIn(BaseModel):
    mode_id: str


# ---------------- Seed data ----------------
GAME_MODES = [
    {"id": "classic-1v1-50", "category": "classic", "title": "Classic 1v1", "players": 2, "entry": 50, "prize": 90, "color": "#EF4444"},
    {"id": "classic-1v1-100", "category": "classic", "title": "Classic 1v1", "players": 2, "entry": 100, "prize": 180, "color": "#EF4444"},
    {"id": "classic-1v1-500", "category": "classic", "title": "Classic 1v1 Pro", "players": 2, "entry": 500, "prize": 940, "color": "#EF4444"},
    {"id": "classic-1v3-50", "category": "classic", "title": "Classic 1v3", "players": 4, "entry": 50, "prize": 170, "color": "#3B82F6"},
    {"id": "classic-1v3-200", "category": "classic", "title": "Classic 1v3 Pro", "players": 4, "entry": 200, "prize": 700, "color": "#3B82F6"},
    {"id": "quick-1v1-20", "category": "quick", "title": "Quick Match", "players": 2, "entry": 20, "prize": 36, "color": "#10B981"},
    {"id": "quick-1v1-100", "category": "quick", "title": "Quick Match", "players": 2, "entry": 100, "prize": 180, "color": "#10B981"},
    {"id": "tour-mega", "category": "tournament", "title": "Mega Tournament", "players": 16, "entry": 250, "prize": 3500, "color": "#EAB308"},
    {"id": "tour-daily", "category": "tournament", "title": "Daily Cup", "players": 8, "entry": 100, "prize": 700, "color": "#EAB308"},
]


# ---------------- Routes ----------------
@api_router.get("/")
async def root():
    return {"message": "Elite Play BD API", "ok": True}


@api_router.post("/auth/register", response_model=AuthOut)
async def register(payload: RegisterIn):
    existing = await db.users.find_one({"phone": payload.phone})
    if existing:
        raise HTTPException(status_code=400, detail="Phone already registered")
    user_id = str(uuid.uuid4())
    user_doc = {
        "id": user_id,
        "name": payload.name,
        "phone": payload.phone,
        "password": hash_password(payload.password),
        "balance": 100.0,  # welcome bonus
        "matches_played": 0,
        "matches_won": 0,
        "total_won": 0.0,
        "referral_code": payload.phone[-6:].upper(),
        "avatar_index": random.randint(0, 3),
        "created_at": now_iso(),
    }
    await db.users.insert_one(user_doc)

    # Welcome bonus tx
    await db.transactions.insert_one({
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "type": "bonus",
        "amount": 100.0,
        "method": "Welcome Bonus",
        "status": "success",
        "created_at": now_iso(),
    })

    user_doc.pop("password", None)
    user_doc.pop("_id", None)
    return {"token": create_token(user_id), "user": user_doc}


@api_router.post("/auth/login", response_model=AuthOut)
async def login(payload: LoginIn):
    user = await db.users.find_one({"phone": payload.phone})
    if not user or user.get("password") != hash_password(payload.password):
        raise HTTPException(status_code=401, detail="Invalid phone or password")
    user.pop("password", None)
    user.pop("_id", None)
    return {"token": create_token(user["id"]), "user": user}


@api_router.get("/me")
async def me(user=Depends(get_current_user)):
    return user


@api_router.get("/modes")
async def list_modes():
    return GAME_MODES


@api_router.post("/wallet/deposit")
async def deposit(payload: DepositIn, user=Depends(get_current_user)):
    if payload.amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid amount")
    new_balance = float(user["balance"]) + payload.amount
    await db.users.update_one({"id": user["id"]}, {"$set": {"balance": new_balance}})
    tx_id = str(uuid.uuid4())
    await db.transactions.insert_one({
        "id": tx_id,
        "user_id": user["id"],
        "type": "deposit",
        "amount": payload.amount,
        "method": payload.method,
        "status": "success",
        "created_at": now_iso(),
    })
    return {"ok": True, "balance": new_balance, "tx_id": tx_id}


@api_router.post("/wallet/withdraw")
async def withdraw(payload: WithdrawIn, user=Depends(get_current_user)):
    if payload.amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid amount")
    if payload.amount > float(user["balance"]):
        raise HTTPException(status_code=400, detail="Insufficient balance")
    new_balance = float(user["balance"]) - payload.amount
    await db.users.update_one({"id": user["id"]}, {"$set": {"balance": new_balance}})
    tx_id = str(uuid.uuid4())
    await db.transactions.insert_one({
        "id": tx_id,
        "user_id": user["id"],
        "type": "withdraw",
        "amount": -payload.amount,
        "method": f"{payload.method} - {payload.account}",
        "status": "pending",
        "created_at": now_iso(),
    })
    return {"ok": True, "balance": new_balance, "tx_id": tx_id}


@api_router.get("/wallet/transactions")
async def list_transactions(user=Depends(get_current_user)):
    items = await db.transactions.find(
        {"user_id": user["id"]}, {"_id": 0}
    ).sort("created_at", -1).to_list(100)
    return items


@api_router.post("/match/join")
async def join_match(payload: JoinMatchIn, user=Depends(get_current_user)):
    mode = next((m for m in GAME_MODES if m["id"] == payload.mode_id), None)
    if not mode:
        raise HTTPException(status_code=404, detail="Mode not found")
    entry = float(mode["entry"])
    if entry > float(user["balance"]):
        raise HTTPException(status_code=400, detail="Insufficient balance to join")

    # Simulate match outcome (50% win for 1v1, lower for multi)
    win_prob = 0.5 if mode["players"] == 2 else (1.0 / mode["players"]) * 1.4
    won = random.random() < win_prob
    payout = float(mode["prize"]) if won else 0.0
    delta = payout - entry

    new_balance = float(user["balance"]) + delta
    inc_won = 1 if won else 0
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"balance": new_balance},
         "$inc": {"matches_played": 1, "matches_won": inc_won, "total_won": payout}},
    )

    match_id = str(uuid.uuid4())
    match_doc = {
        "id": match_id,
        "user_id": user["id"],
        "mode_id": mode["id"],
        "mode_title": mode["title"],
        "entry": entry,
        "prize": float(mode["prize"]),
        "result": "won" if won else "lost",
        "delta": delta,
        "opponent": random.choice(["Rakib", "Sumon", "Tanvir", "Sajid", "Imran", "Hasan", "Nayeem", "Rifat"]),
        "created_at": now_iso(),
    }
    await db.matches.insert_one(match_doc)

    await db.transactions.insert_one({
        "id": str(uuid.uuid4()),
        "user_id": user["id"],
        "type": "match",
        "amount": delta,
        "method": f"Match - {mode['title']}",
        "status": "success",
        "created_at": now_iso(),
    })

    match_doc.pop("_id", None)
    return {"ok": True, "balance": new_balance, "match": match_doc}


@api_router.get("/match/history")
async def match_history(user=Depends(get_current_user)):
    items = await db.matches.find(
        {"user_id": user["id"]}, {"_id": 0}
    ).sort("created_at", -1).to_list(100)
    return items


@api_router.get("/stats")
async def stats(user=Depends(get_current_user)):
    played = int(user.get("matches_played", 0))
    won = int(user.get("matches_won", 0))
    return {
        "matches_played": played,
        "matches_won": won,
        "win_rate": round((won / played) * 100) if played else 0,
        "total_won": float(user.get("total_won", 0)),
    }


app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
