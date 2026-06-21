"""Routes de gestion des providers LLM."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core import database as db
from core import plans
from src import llm, provider_access

router = APIRouter(prefix="/api/providers", tags=["providers"])


class ProviderIn(BaseModel):
    name: str
    base_url: str
    api_key: str = ""
    model: str = ""
    embed_model: str = ""


@router.get("")
def get_providers(request: Request):
    user = getattr(request.state, "user", None)
    is_admin = bool(user and user.get("is_admin"))
    plan = plans.normalize_plan(user.get("plan") if user else None, is_admin)
    # On masque la cle API dans la reponse.
    out = []
    for p in provider_access.visible_providers(plan, is_admin):
        out.append({
            "id": p["id"], "name": p["name"], "base_url": p["base_url"],
            "model": p["model"], "embed_model": p.get("embed_model", ""),
            "has_key": bool(p["api_key"]),
            "premium_managed": bool(p.get("premium_managed")),
        })
    return out


@router.post("")
def create_provider(body: ProviderIn, request: Request):
    user = getattr(request.state, "user", None)
    if not user or not user.get("is_admin"):
        raise HTTPException(403, "Reserve aux administrateurs")
    p = db.add_provider(
        body.name,
        llm.normalize_base(body.base_url),
        body.api_key,
        body.model,
        body.embed_model,
    )
    return {"id": p["id"], "name": p["name"], "base_url": p["base_url"],
            "model": p["model"], "embed_model": p["embed_model"]}


@router.delete("/{pid}")
def remove_provider(pid: str, request: Request):
    user = getattr(request.state, "user", None)
    if not user or not user.get("is_admin"):
        raise HTTPException(403, "Reserve aux administrateurs")
    db.delete_provider(pid)
    return {"ok": True}


@router.get("/{pid}/models")
async def provider_models(pid: str, request: Request):
    user = getattr(request.state, "user", None)
    is_admin = bool(user and user.get("is_admin"))
    plan = plans.normalize_plan(user.get("plan") if user else None, is_admin)
    p = provider_access.resolve_provider(pid, plan, is_admin)
    if not p:
        raise HTTPException(404, "Provider introuvable")
    try:
        models = await llm.list_models(p["base_url"], p["api_key"])
    except llm.LLMError as e:
        raise HTTPException(502, str(e))
    return {"models": models}
