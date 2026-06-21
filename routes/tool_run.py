"""Route /api/tool/run — exécute un outil directement depuis l'UI (ex: Deep Research)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core import billing
from core import database as db
from core import plans
from src import premium_provider, provider_access
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
    if body.tool in plans.ADMIN_TOOLS and not is_admin:
        raise HTTPException(403, "Administrateur requis pour utiliser cet outil")
    if not plans.tool_allowed(body.tool, plan, is_admin):
        raise HTTPException(403, "Plan premium requis pour utiliser cet outil")
    cost = plans.tool_credit_cost(body.tool, is_admin)
    if not is_admin and int(user.get("credit_balance") or 0) < cost:
        raise HTTPException(402, f"Solde de credits insuffisant ({cost} credits requis)")

    # Build ctx identique à celui de la boucle agent
    provider_row = None
    if body.provider:
        provider_row = provider_access.resolve_provider(body.provider, plan, is_admin)
    if not provider_row:
        # fallback : premier provider disponible
        providers = provider_access.visible_providers(plan, is_admin)
        provider_row = providers[0] if providers else None

    ctx: dict = {
        "user_id":  user["id"],
        "is_admin": is_admin,
        "plan": plan,
        "provider": provider_row,
        "model":    body.model or (provider_row.get("default_model") if provider_row else None),
    }
    managed_openai = premium_provider.is_managed_provider(provider_row)
    managed_limits: dict = {}
    if managed_openai:
        try:
            managed_limits = premium_provider.check_limits(
                user["id"],
                billing.estimate_text_tokens(str(body.args or {})),
                None if is_admin else int(user.get("credit_balance") or 0),
            )
            ctx["max_tokens"] = int(managed_limits["max_output_tokens"])
        except ValueError as e:
            raise HTTPException(402, str(e)) from None

    result = await execute_tool(body.tool, body.args, ctx)
    args_tokens = billing.estimate_text_tokens(str(body.args or {}))
    result_tokens = billing.estimate_text_tokens(result)
    usage = {
        "credits_spent": 0,
        "balance": None,
        "cost": cost,
    }
    if cost and not is_admin:
        spent, balance = db.spend_user_credits(
            user["id"],
            cost,
            input_tokens=args_tokens,
            output_tokens=result_tokens,
            reason=f"tool:{body.tool}",
            meta={
                "tool": body.tool,
                "model": ctx.get("model"),
                "provider_id": body.provider,
                **managed_limits,
            },
        )
        usage = {
            "credits_spent": spent,
            "balance": balance,
            "cost": cost,
            "input_tokens": args_tokens,
            "output_tokens": result_tokens,
        }
    elif managed_openai:
        db.record_usage_event(
            user["id"],
            input_tokens=args_tokens,
            output_tokens=result_tokens,
            reason=f"tool:{body.tool}:admin",
            meta={"tool": body.tool, "model": ctx.get("model"), "provider_id": body.provider, **managed_limits},
        )
    return {"result": result, "usage": usage}


@router.get("/list")
def list_tools(request: Request):
    """Retourne la liste des outils disponibles (specs OpenAI)."""
    from src.tools import openai_tool_specs
    user = getattr(request.state, "user", None)
    is_admin = bool(user.get("is_admin")) if user else False
    plan = plans.normalize_plan(user.get("plan") if user else None, is_admin)
    return openai_tool_specs(is_admin, plan)
