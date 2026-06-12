"""Routes de gestion des providers LLM."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core import database as db
from src import llm

router = APIRouter(prefix="/api/providers", tags=["providers"])


class ProviderIn(BaseModel):
    name: str
    base_url: str
    api_key: str = ""
    model: str = ""
    embed_model: str = ""


@router.get("")
def get_providers():
    # On masque la cle API dans la reponse.
    out = []
    for p in db.list_providers():
        out.append({
            "id": p["id"], "name": p["name"], "base_url": p["base_url"],
            "model": p["model"], "embed_model": p.get("embed_model", ""),
            "has_key": bool(p["api_key"]),
        })
    return out


@router.post("")
def create_provider(body: ProviderIn):
    p = db.add_provider(body.name, body.base_url, body.api_key, body.model, body.embed_model)
    return {"id": p["id"], "name": p["name"], "base_url": p["base_url"],
            "model": p["model"], "embed_model": p["embed_model"]}


@router.delete("/{pid}")
def remove_provider(pid: str):
    db.delete_provider(pid)
    return {"ok": True}


@router.get("/{pid}/models")
async def provider_models(pid: str):
    p = db.get_provider(pid)
    if not p:
        raise HTTPException(404, "Provider introuvable")
    try:
        models = await llm.list_models(p["base_url"], p["api_key"])
    except llm.LLMError as e:
        raise HTTPException(502, str(e))
    return {"models": models}
