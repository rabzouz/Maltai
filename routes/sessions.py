"""Routes de gestion des sessions et de leurs messages.

Chaque session est rattachee a son proprietaire (user_id). Un utilisateur ne
voit et ne manipule que ses propres sessions. En mode local sans auth
(user_id None), on opere sur les sessions non rattachees.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core import database as db

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


def _uid(request: Request) -> str | None:
    user = getattr(request.state, "user", None)
    return user["id"] if user else None


def _require_session(sid: str, request: Request) -> None:
    if not db.session_accessible(sid, _uid(request)):
        raise HTTPException(404, "Session introuvable")


class SessionIn(BaseModel):
    provider_id: str | None = None
    model: str | None = None


class RenameIn(BaseModel):
    title: str


@router.get("")
def get_sessions(request: Request):
    return db.list_sessions(_uid(request))


@router.post("")
def create_session(body: SessionIn, request: Request):
    return db.create_session(_uid(request), body.provider_id, body.model)


@router.get("/{sid}/messages")
def get_messages(sid: str, request: Request):
    _require_session(sid, request)
    return db.list_messages(sid)


@router.patch("/{sid}")
def rename(sid: str, body: RenameIn, request: Request):
    _require_session(sid, request)
    db.rename_session(sid, body.title.strip() or "Discussion")
    return {"ok": True}


@router.delete("/{sid}")
def remove(sid: str, request: Request):
    _require_session(sid, request)
    db.delete_session(sid)
    return {"ok": True}


class TruncateIn(BaseModel):
    message_id: str


@router.post("/{sid}/truncate")
def truncate(sid: str, body: TruncateIn, request: Request):
    """Supprime le message vise et tout ce qui suit (edition d'un message)."""
    _require_session(sid, request)
    removed = db.truncate_from(sid, body.message_id)
    if removed == 0:
        raise HTTPException(404, "Message introuvable")
    return {"ok": True, "removed": removed}


@router.post("/{sid}/regenerate-prep")
def regenerate_prep(sid: str, request: Request):
    """Supprime le dernier echange et retourne le message user a rejouer."""
    _require_session(sid, request)
    content = db.pop_last_exchange(sid)
    if content is None:
        raise HTTPException(404, "Rien a regenerer")
    # Retire le marqueur de fichiers joints : ils ne sont pas rejoues.
    content = re.sub(r"\n\[fichiers joints : [^\]]*\]$", "", content)
    return {"content": content}
