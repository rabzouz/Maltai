"""Routes de gestion des serveurs MCP."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core import database as db
from src import mcp

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


class MCPServerIn(BaseModel):
    name: str
    url: str
    auth_token: str = ""


class EnabledIn(BaseModel):
    enabled: bool


@router.get("")
def get_servers():
    out = []
    for s in db.list_mcp_servers():
        out.append({
            "id": s["id"], "name": s["name"], "url": s["url"],
            "enabled": bool(s["enabled"]), "has_token": bool(s["auth_token"]),
        })
    return out


@router.post("")
def create_server(body: MCPServerIn):
    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "URL invalide (http/https requis)")
    return db.add_mcp_server(body.name.strip(), url, body.auth_token.strip())


@router.patch("/{sid}")
def toggle_server(sid: str, body: EnabledIn):
    if not db.get_mcp_server(sid):
        raise HTTPException(404, "Serveur introuvable")
    db.set_mcp_enabled(sid, body.enabled)
    return {"ok": True}


@router.delete("/{sid}")
def remove_server(sid: str):
    db.delete_mcp_server(sid)
    return {"ok": True}


@router.get("/{sid}/tools")
async def server_tools(sid: str):
    """Teste la connexion et liste les outils exposes par le serveur."""
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
