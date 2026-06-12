"""Routes de gestion des sessions et de leurs messages."""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core import database as db

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class SessionIn(BaseModel):
    provider_id: str | None = None
    model: str | None = None


class RenameIn(BaseModel):
    title: str


@router.get("")
def get_sessions():
    return db.list_sessions()


@router.post("")
def create_session(body: SessionIn):
    return db.create_session(body.provider_id, body.model)


@router.get("/{sid}/messages")
def get_messages(sid: str):
    return db.list_messages(sid)


@router.patch("/{sid}")
def rename(sid: str, body: RenameIn):
    db.rename_session(sid, body.title.strip() or "Discussion")
    return {"ok": True}


@router.delete("/{sid}")
def remove(sid: str):
    db.delete_session(sid)
    return {"ok": True}


class TruncateIn(BaseModel):
    message_id: str


@router.post("/{sid}/truncate")
def truncate(sid: str, body: TruncateIn):
    """Supprime le message vise et tout ce qui suit (edition d'un message)."""
    removed = db.truncate_from(sid, body.message_id)
    if removed == 0:
        raise HTTPException(404, "Message introuvable")
    return {"ok": True, "removed": removed}


@router.post("/{sid}/regenerate-prep")
def regenerate_prep(sid: str):
    """Supprime le dernier echange et retourne le message user a rejouer."""
    content = db.pop_last_exchange(sid)
    if content is None:
        raise HTTPException(404, "Rien a regenerer")
    # Retire le marqueur de fichiers joints : ils ne sont pas rejoues.
    content = re.sub(r"\n\[fichiers joints : [^\]]*\]$", "", content)
    return {"content": content}
