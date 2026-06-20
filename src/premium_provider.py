"""Provider OpenAI virtuel reserve aux comptes Premium/Admin."""
from __future__ import annotations

import calendar
import time

from core import database as db
from core import plans
from core.config import settings
from src import llm

PREMIUM_PROVIDER_ID = "__maltai_premium_openai__"


def is_configured() -> bool:
    return bool(settings.PREMIUM_OPENAI_API_KEY.strip())


def allowed(plan: str | None, is_admin: bool = False) -> bool:
    return is_configured() and plans.can_use_tools(plan, is_admin)


def provider_row() -> dict:
    return {
        "id": PREMIUM_PROVIDER_ID,
        "name": settings.PREMIUM_OPENAI_NAME,
        "base_url": llm.normalize_base(settings.PREMIUM_OPENAI_BASE_URL),
        "api_key": settings.PREMIUM_OPENAI_API_KEY,
        "model": settings.PREMIUM_OPENAI_MODEL,
        "embed_model": settings.PREMIUM_OPENAI_EMBED_MODEL,
        "premium_managed": True,
    }


def public_provider() -> dict:
    p = provider_row()
    return {
        "id": p["id"],
        "name": p["name"],
        "base_url": p["base_url"],
        "model": p["model"],
        "embed_model": p["embed_model"],
        "has_key": True,
        "premium_managed": True,
    }


def resolve(pid: str | None, plan: str | None, is_admin: bool = False) -> dict | None:
    if pid == PREMIUM_PROVIDER_ID and allowed(plan, is_admin):
        return provider_row()
    return None


def is_managed_provider(provider: dict | None) -> bool:
    return bool(provider and provider.get("premium_managed"))


def month_start_ts() -> float:
    now = time.localtime()
    return float(time.mktime((now.tm_year, now.tm_mon, 1, 0, 0, 0, 0, 0, -1)))


def projected_tokens(input_tokens: int) -> int:
    return max(1, int(input_tokens)) + max(1, settings.MANAGED_OPENAI_MAX_OUTPUT_TOKENS)


def check_limits(user_id: str, input_tokens: int, credit_balance: int | None = None) -> dict:
    """Retourne un resume des limites ou leve ValueError si la requete doit etre bloquee."""
    if input_tokens > settings.MANAGED_OPENAI_MAX_INPUT_TOKENS:
        raise ValueError(
            f"Requete trop longue pour le provider Premium OpenAI "
            f"({input_tokens} tokens estimes, limite {settings.MANAGED_OPENAI_MAX_INPUT_TOKENS})."
        )

    projected = projected_tokens(input_tokens)
    if credit_balance is not None and credit_balance < projected:
        raise ValueError(
            f"Solde insuffisant pour cette requete Premium OpenAI "
            f"({projected} credits requis, solde {credit_balance})."
        )

    since = month_start_ts()
    user_used = db.managed_openai_monthly_tokens(user_id=user_id, since_ts=since)
    global_used = db.managed_openai_monthly_tokens(since_ts=since)
    user_limit = max(0, settings.MANAGED_OPENAI_MONTHLY_USER_TOKEN_LIMIT)
    global_limit = max(0, settings.MANAGED_OPENAI_MONTHLY_GLOBAL_TOKEN_LIMIT)

    if user_limit and user_used + projected > user_limit:
        raise ValueError(
            f"Limite mensuelle Premium OpenAI atteinte "
            f"({user_used}/{user_limit} tokens estimes utilises)."
        )
    if global_limit and global_used + projected > global_limit:
        raise ValueError(
            "Budget mensuel global OpenAI Maltai atteint. Reessaie plus tard ou contacte l'administrateur."
        )

    return {
        "managed_openai": True,
        "monthly_user_used": user_used,
        "monthly_user_limit": user_limit,
        "monthly_global_used": global_used,
        "monthly_global_limit": global_limit,
        "projected_tokens": projected,
        "max_output_tokens": settings.MANAGED_OPENAI_MAX_OUTPUT_TOKENS,
    }
