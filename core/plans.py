"""Subscription plans and feature gates."""
from __future__ import annotations

VALID_PLANS = {"basic", "premium", "admin"}
PREMIUM_PLANS = {"premium", "admin"}
ADMIN_TOOLS = {"shell", "python_exec"}


def normalize_plan(plan: str | None, is_admin: bool = False) -> str:
    if is_admin:
        return "admin"
    p = (plan or "basic").strip().lower()
    return p if p in VALID_PLANS else "basic"


def can_use_tools(plan: str | None, is_admin: bool = False) -> bool:
    return normalize_plan(plan, is_admin) in PREMIUM_PLANS


def tool_allowed(name: str, plan: str | None, is_admin: bool = False) -> bool:
    effective = normalize_plan(plan, is_admin)
    if effective not in PREMIUM_PLANS:
        return False
    if name in ADMIN_TOOLS and not is_admin:
        return False
    return True
