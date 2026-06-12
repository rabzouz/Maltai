"""API externe Maltai — pour brancher OpenClaw, scripts, ou tout autre systeme.

Auth par cle API (header X-Api-Key ou Authorization: Bearer). Les cles sont
gerees dans Reglages → API externe et stockees hashees-comparables en kv.

POST /api/external/chat
  { "session_key": "openclaw-main",   # identifiant libre -> session dediee
    "message": "...",
    "agent": false }                   # optionnel : mode agent
  -> { "session_id": "...", "answer": "..." }

Exemple cote OpenClaw : une skill HTTP qui POST sur cet endpoint permet a ton
assistant OpenClaw d'interroger Maltai (sa memoire, ses outils MCP...).
"""
from __future__ import annotations

import hmac
import secrets as pysecrets

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core import database as db
from src import connector

router = APIRouter(prefix="/api/external", tags=["external"])

KV_KEY = "external_api_keys"  # liste de {"name": str, "key": str}


def _keys() -> list[dict]:
    return db.kv_get(KV_KEY, [])


def _check_key(request: Request) -> None:
    provided = request.headers.get("X-Api-Key", "")
    if not provided:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            provided = auth[len("Bearer "):]
    if not provided:
        raise HTTPException(401, "Cle API manquante (X-Api-Key)")
    for entry in _keys():
        if hmac.compare_digest(entry.get("key", ""), provided):
            return
    raise HTTPException(403, "Cle API invalide")


class ChatIn(BaseModel):
    session_key: str
    message: str
    agent: bool = False
    provider_id: str | None = None
    model: str | None = None


class KeyIn(BaseModel):
    name: str


# --- Gestion des cles (UI, protegee par l'auth cookie habituelle) -----------

@router.get("/keys")
def list_keys(request: Request):
    if getattr(request.state, "user", None) is None:
        raise HTTPException(401, "Non authentifie")
    return [{"name": k["name"], "preview": k["key"][:8] + "…"} for k in _keys()]


@router.post("/keys")
def create_key(body: KeyIn, request: Request):
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(401, "Non authentifie")
    if not user.get("is_admin"):
        raise HTTPException(403, "Reserve aux administrateurs")
    key = "mlt_" + pysecrets.token_urlsafe(32)
    keys = _keys()
    keys.append({"name": body.name.strip() or "cle", "key": key})
    db.kv_set(KV_KEY, keys)
    # La cle complete n'est montree qu'une seule fois, a la creation.
    return {"name": body.name, "key": key}


@router.delete("/keys/{name}")
def delete_key(name: str, request: Request):
    user = getattr(request.state, "user", None)
    if user is None or not user.get("is_admin"):
        raise HTTPException(403, "Reserve aux administrateurs")
    keys = [k for k in _keys() if k["name"] != name]
    db.kv_set(KV_KEY, keys)
    return {"ok": True}


# --- Endpoint de chat (auth par cle API, pas par cookie) ---------------------

@router.post("/chat")
async def external_chat(body: ChatIn, request: Request):
    _check_key(request)

    # Session dediee par session_key (continuite multi-appels).
    bind_key = f"external_session:{body.session_key}"
    session_id = db.kv_get(bind_key)
    if not session_id or not any(s["id"] == session_id for s in db.list_sessions()):
        session = db.create_session(body.provider_id, body.model)
        session_id = session["id"]
        db.rename_session(session_id, f"API {body.session_key}"[:60])
        db.kv_set(bind_key, session_id)

    try:
        answer = await connector.run_turn(
            session_id, body.message,
            provider_id=body.provider_id, model=body.model,
            use_agent=body.agent,
            is_admin=False,  # jamais de shell via l'API externe
        )
    except connector.ConnectorError as e:
        raise HTTPException(502, str(e))
    return {"session_id": session_id, "answer": answer}
