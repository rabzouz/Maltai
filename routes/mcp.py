"""Routes de gestion des serveurs MCP.

L'ajout/modification/suppression d'un serveur MCP, ainsi que le test de
connexion (qui declenche une requete sortante), sont reserves aux
administrateurs : un serveur MCP est une URL appelee cote serveur et
constitue donc un vecteur SSRF s'il est ouvert a tous les comptes.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core import database as db
from core.config import settings
from src import mcp

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


def _require_admin(request: Request) -> None:
    if not settings.AUTH_ENABLED:
        return
    user = getattr(request.state, "user", None)
    if not user or not user.get("is_admin"):
        raise HTTPException(403, "Reserve aux administrateurs")


class MCPServerIn(BaseModel):
    name: str
    url: str
    auth_token: str = ""


class EnabledIn(BaseModel):
    enabled: bool


@router.get("")
def get_servers(request: Request):
    _require_admin(request)
    out = []
    for s in db.list_mcp_servers():
        out.append({
            "id": s["id"], "name": s["name"], "url": s["url"],
            "enabled": bool(s["enabled"]), "has_token": bool(s["auth_token"]),
        })
    return out


@router.post("")
def create_server(body: MCPServerIn, request: Request):
    _require_admin(request)
    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "URL invalide (http/https requis)")
    return db.add_mcp_server(body.name.strip(), url, body.auth_token.strip())


@router.patch("/{sid}")
def toggle_server(sid: str, body: EnabledIn, request: Request):
    _require_admin(request)
    if not db.get_mcp_server(sid):
        raise HTTPException(404, "Serveur introuvable")
    db.set_mcp_enabled(sid, body.enabled)
    return {"ok": True}


@router.delete("/{sid}")
def remove_server(sid: str, request: Request):
    _require_admin(request)
    db.delete_mcp_server(sid)
    return {"ok": True}


@router.get("/{sid}/tools")
async def server_tools(sid: str, request: Request):
    """Teste la connexion et liste les outils exposes par le serveur."""
    _require_admin(request)
    srv = db.get_mcp_server(sid)
    if not srv:
        raise HTTPException(404, "Serveur introuvable")
    client = mcp.MCPClient(srv["url"], srv["auth_token"])
    try:
        await client.initialize()
        tools = await client.list_tools()
    except mcp.MCPError as e:
        raise HTTPException(502, str(e))
    return {
        "tools": [
            {"name": t.get("name", ""), "description": (t.get("description") or "")[:200]}
            for t in tools
        ]
    }
