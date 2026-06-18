"""Route mémoire : compter, lister, rechercher et effacer les souvenirs."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from core import database as db
from core.config import settings

router = APIRouter(prefix="/api/memory", tags=["memory"])


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
def memory_items(request: Request, limit: int = 50, q: str = ""):
    return {
        "items": db.list_memories(_uid(request), limit=limit, query=q),
        "count": db.count_memories(_uid(request)),
    }


@router.delete("")
def memory_clear(request: Request):
    removed = db.clear_memories(_uid(request))
    return {"ok": True, "removed": removed}


@router.delete("/{memory_id}")
def memory_delete(memory_id: str, request: Request):
    ok = db.delete_memory(_uid(request), memory_id)
    if not ok:
        raise HTTPException(404, "Souvenir introuvable")
    return {"ok": True, "id": memory_id}
