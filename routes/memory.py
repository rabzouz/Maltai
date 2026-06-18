"""Route mémoire : compter, lister, rechercher et effacer les souvenirs."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core import database as db
from core.config import settings

router = APIRouter(prefix="/api/memory", tags=["memory"])


class PinIn(BaseModel):
    pinned: bool = True


def _uid(request: Request) -> str | None:
    user = getattr(request.state, "user", None)
    return user["id"] if user else None


@router.get("")
def memory_status(request: Request):
    return {
        "enabled": settings.MEMORY_ENABLED,
        "count": db.count_memories(_uid(request)),
        "top_k": settings.MEMORY_TOP_K,
    }


@router.get("/items")
def memory_items(request: Request, limit: int = 50, q: str = "", role: str = "", pinned: str = ""):
    return {
        "items": db.list_memories(_uid(request), limit=limit, query=q, role=role, pinned=pinned),
        "count": db.count_memories(_uid(request)),
    }


@router.delete("/filtered")
def memory_delete_filtered(request: Request, q: str = "", role: str = ""):
    removed = db.delete_memories_filtered(_uid(request), query=q, role=role)
    return {"ok": True, "removed": removed}


@router.delete("")
def memory_clear(request: Request):
    removed = db.clear_memories(_uid(request))
    return {"ok": True, "removed": removed}


@router.patch("/{memory_id}/pin")
def memory_pin(memory_id: str, body: PinIn, request: Request):
    ok = db.set_memory_pinned(_uid(request), memory_id, body.pinned)
    if not ok:
        raise HTTPException(404, "Souvenir introuvable")
    return {"ok": True, "id": memory_id, "pinned": body.pinned}


@router.delete("/{memory_id}")
def memory_delete(memory_id: str, request: Request):
    ok = db.delete_memory(_uid(request), memory_id)
    if not ok:
        raise HTTPException(404, "Souvenir introuvable")
    return {"ok": True, "id": memory_id}
