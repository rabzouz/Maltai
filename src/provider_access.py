"""Regles d'acces aux providers LLM selon plan et role."""
from __future__ import annotations

from urllib.parse import urlparse

from core import database as db
from core import plans
from src import premium_provider

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal", "10.0.1.1"}
LOCAL_KEYS = {"", "ollama", "local"}


def _host(base_url: str) -> str:
    try:
        return (urlparse(base_url).hostname or "").lower()
    except Exception:
        return ""


def is_local_or_public(provider: dict) -> bool:
    """Basic peut utiliser seulement les providers sans vraie cle payante."""
    key = str(provider.get("api_key") or "").strip().lower()
    if key not in LOCAL_KEYS:
        return False
    host = _host(str(provider.get("base_url") or ""))
    if host in LOCAL_HOSTS:
        return True
    # Une API sans cle ne donne pas acces a une cle secrete geree par Maltai.
    return key == ""


def can_use_provider(provider: dict | None, plan: str | None, is_admin: bool = False) -> bool:
    if not provider:
        return False
    if is_admin:
        return True
    if premium_provider.is_managed_provider(provider):
        return premium_provider.allowed(plan, is_admin)
    if plans.can_use_tools(plan, is_admin):
        return True
    return is_local_or_public(provider)


def visible_providers(plan: str | None, is_admin: bool = False) -> list[dict]:
    out: list[dict] = []
    if premium_provider.allowed(plan, is_admin):
        out.append(premium_provider.provider_row())
    for provider in db.list_providers():
        if can_use_provider(provider, plan, is_admin):
            out.append(provider)
    return out


def resolve_provider(provider_id: str | None, plan: str | None, is_admin: bool = False) -> dict | None:
    provider = premium_provider.resolve(provider_id, plan, is_admin) or db.get_provider(provider_id or "")
    if can_use_provider(provider, plan, is_admin):
        return provider
    return None
