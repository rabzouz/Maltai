"""Route mémoire : compter et effacer les souvenirs de l'utilisateur."""
from __future__ import annotations

from fastapi import APIRouter, Request

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


@router.delete("")
def memory_clear(request: Request):
    removed = db.clear_memories(_uid(request))
    return {"ok": True, "removed": removed}
