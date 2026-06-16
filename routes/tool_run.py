"""Route /api/tool/run — exécute un outil directement depuis l'UI (ex: Deep Research)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core import billing
from core import database as db
from core import plans
from src.tools import execute_tool, TOOLS

router = APIRouter(prefix="/api/tool", tags=["tool_run"])


class ToolRunIn(BaseModel):
    tool: str
    args: dict = {}
    provider: str | None = None   # provider id
    model: str | None = None


@router.post("/run")
async def run_tool(request: Request, body: ToolRunIn):
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Non authentifié")

    if body.tool not in TOOLS:
        raise HTTPException(400, f"Outil inconnu : {body.tool}")
    is_admin = bool(user.get("is_admin"))
    plan = plans.normalize_plan(user.get("plan"), is_admin)
    if not plans.tool_allowed(body.tool, plan, is_admin):
        raise HTTPException(403, "Plan premium requis pour utiliser cet outil")
    cost = plans.tool_credit_cost(body.tool, is_admin)
    if not is_admin and int(user.get("credit_balance") or 0) < cost:
        raise HTTPException(402, f"Solde de credits insuffisant ({cost} credits requis)")

    # Build ctx identique à celui de la boucle agent
    provider_row = None
    if body.provider:
        provider_row = db.get_provider(body.provider)
    if not provider_row:
        # fallback : premier provider disponible
        providers = db.list_providers()
        provider_row = providers[0] if providers else None

    ctx: dict = {
        "user_id":  user["id"],
        "is_admin": is_admin,
        "plan": plan,
        "provider": provider_row,
        "model":    body.model or (provider_row.get("default_model") if provider_row else None),
    }

    result = await execute_tool(body.tool, body.args, ctx)
    usage = {
        "credits_spent": 0,
        "balance": None,
        "cost": cost,
    }
    if cost and not is_admin:
        args_tokens = billing.estimate_text_tokens(str(body.args or {}))
        result_tokens = billing.estimate_text_tokens(result)
        spent, balance = db.spend_user_credits(
            user["id"],
            cost,
            input_tokens=args_tokens,
            output_tokens=result_tokens,
            reason=f"tool:{body.tool}",
            meta={"tool": body.tool, "model": ctx.get("model")},
        )
        usage = {
            "credits_spent": spent,
            "balance": balance,
            "cost": cost,
            "input_tokens": args_tokens,
            "output_tokens": result_tokens,
        }
    return {"result": result, "usage": usage}


@router.get("/list")
def list_tools(request: Request):
    """Retourne la liste des outils disponibles (specs OpenAI)."""
    from src.tools import openai_tool_specs
    user = getattr(request.state, "user", None)
    is_admin = bool(user.get("is_admin")) if user else False
    plan = plans.normalize_plan(user.get("plan") if user else None, is_admin)
    return openai_tool_specs(is_admin, plan)
