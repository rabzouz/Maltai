"""Routes de gestion des modeles Ollama locaux."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.config import settings
from src import ollama

router = APIRouter(prefix="/api/ollama", tags=["ollama"])


class ModelIn(BaseModel):
    name: str


def _require_admin(request: Request) -> None:
    if not settings.AUTH_ENABLED:
        return
    user = getattr(request.state, "user", None)
    if not user or not user.get("is_admin"):
        raise HTTPException(403, "Admin requis")


@router.get("/models")
async def models():
    try:
        return {
            "base_url": ollama.configured_base_url(),
            "models": await ollama.list_models(),
        }
    except ollama.OllamaError as e:
        raise HTTPException(502, str(e))


@router.post("/pull")
async def pull(body: ModelIn, request: Request):
    _require_admin(request)
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Nom de modele requis")

    async def events():
        try:
            async for item in ollama.pull_model(name):
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        except ollama.OllamaError as e:
            yield f"event: error\ndata: {json.dumps({'message': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@router.delete("/models/{name:path}")
async def delete(name: str, request: Request):
    _require_admin(request)
    if not name.strip():
        raise HTTPException(400, "Nom de modele requis")
    try:
        await ollama.delete_model(name)
    except ollama.OllamaError as e:
        raise HTTPException(502, str(e))
    return {"ok": True}
