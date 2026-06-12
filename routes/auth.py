"""Routes d'authentification."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from core import auth
from core.config import settings

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


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
    return {"id": user["id"], "username": user["username"], "is_admin": bool(user["is_admin"])}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Non authentifie")
    return {"id": user["id"], "username": user["username"], "is_admin": bool(user["is_admin"])}


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
