"""Provider OpenAI virtuel reserve aux comptes Premium/Admin."""
from __future__ import annotations

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
