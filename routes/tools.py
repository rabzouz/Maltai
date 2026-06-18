"""Route /api/tools : liste les outils disponibles pour le panneau de l'UI.

Retourne les outils natifs (selon le role) et les outils des serveurs MCP
actifs (interroges en best-effort, avec leur nom prefixe pret a l'emploi).
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from core import database as db
from core import plans
from src import mcp, tools

router = APIRouter(prefix="/api/tools", tags=["tools"])


@router.get("")
async def list_tools(request: Request):
    user = getattr(request.state, "user", None)
    is_admin = bool(user and user.get("is_admin"))
    plan = plans.normalize_plan(user.get("plan") if user else None, is_admin)

    native = []
    for name, t in tools.TOOLS.items():
        admin_only = name in plans.ADMIN_TOOLS
        if not plans.tool_allowed(name, plan, is_admin):
            continue
        native.append({
            "name": name,
            "description": t["spec"].get("description", ""),
            "admin_only": admin_only,
            "credit_cost": plans.tool_credit_cost(name, is_admin),
        })

    mcp_tools = []
    servers = db.list_mcp_servers(enabled_only=True)
    if servers and plans.can_use_tools(plan, is_admin):
        specs, _ = await mcp.load_mcp_tools(servers)
        for s in specs:
            fn = s["function"]
            mcp_tools.append({
                "name": fn["name"],
                "description": (fn.get("description") or "")[:200],
            })

    return {
        "plan": plan,
        "can_use_tools": plans.can_use_tools(plan, is_admin),
        "upgrade_message": "Plan premium requis pour utiliser les outils de l'agent.",
        "native": native,
        "mcp": mcp_tools,
    }
