"""Routes d'authentification."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from core import auth, database as db
from core.config import settings
from core.plans import normalize_plan

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


class PlanIn(BaseModel):
    plan: str


class CreditsIn(BaseModel):
    credits: int
    mode: str = "set"


def _public_user(user: dict) -> dict:
    is_admin = bool(user["is_admin"])
    return {
        "id": user["id"],
        "username": user["username"],
        "is_admin": is_admin,
        "plan": normalize_plan(user.get("plan"), is_admin),
        "credit_balance": None if is_admin else int(user.get("credit_balance") or 0),
    }


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        auth.COOKIE_NAME,
        token,
        max_age=auth.SESSION_TTL,
        httponly=True,
        samesite="lax",
        secure=settings.SECURE_COOKIES,
        path="/",
    )


@router.post("/login")
def login(body: LoginIn, response: Response):
    user = auth.get_user_by_username(body.username.strip())
    if not user or not auth.verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "Identifiants invalides")
    _set_cookie(response, auth.create_token(user["id"]))
    return _public_user(user)


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Non authentifie")
    return _public_user(user)


@router.get("/users")
def users(request: Request):
    user = getattr(request.state, "user", None)
    if not user or not user.get("is_admin"):
        raise HTTPException(403, "Reserve aux administrateurs")
    return [_public_user(u) for u in db.list_users()]


@router.patch("/users/{user_id}/plan")
def set_plan(user_id: str, body: PlanIn, request: Request):
    user = getattr(request.state, "user", None)
    if not user or not user.get("is_admin"):
        raise HTTPException(403, "Reserve aux administrateurs")
    plan = normalize_plan(body.plan, False)
    if plan == "admin":
        raise HTTPException(400, "Le plan admin est reserve aux comptes administrateurs")
    if not db.set_user_plan(user_id, plan):
        raise HTTPException(400, "Impossible de changer le plan : utilisateur introuvable ou administrateur")
    return {"ok": True, "plan": plan}


@router.patch("/users/{user_id}/credits")
def set_credits(user_id: str, body: CreditsIn, request: Request):
    user = getattr(request.state, "user", None)
    if not user or not user.get("is_admin"):
        raise HTTPException(403, "Reserve aux administrateurs")
    if body.mode == "add":
        balance = db.add_user_credits(user_id, body.credits)
    else:
        balance = db.set_user_credits(user_id, body.credits)
    return {"ok": True, "credit_balance": balance}


@router.get("/credits")
def credits(request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Non authentifie")
    return {
        "credit_balance": None if user.get("is_admin") else int(user.get("credit_balance") or 0),
        "ledger": [] if user.get("is_admin") else db.list_credit_ledger(user["id"], 20),
    }


@router.post("/change-password")
def change_password(body: ChangePasswordIn, request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Non authentifie")
    if not auth.verify_password(body.current_password, user["password_hash"]):
        raise HTTPException(403, "Mot de passe actuel incorrect")
    if len(body.new_password) < 8:
        raise HTTPException(400, "8 caracteres minimum")
    auth.change_password(user["id"], body.new_password)
    return {"ok": True}
