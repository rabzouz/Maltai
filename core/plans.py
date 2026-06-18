"""Subscription plans and feature gates."""
from __future__ import annotations

VALID_PLANS = {"basic", "premium", "admin"}
PREMIUM_PLANS = {"premium", "admin"}
ADMIN_TOOLS = {
    "shell",
    "python_exec",
    "git_status",
    "git_branch",
    "git_log",
    "git_diff",
    "git_show",
}
TOOL_CREDIT_COSTS = {
    "calculator": 2,
    "get_datetime": 1,
    "list_files": 3,
    "read_file": 8,
    "write_file": 15,
    "patch_file": 20,
    "code_execute": 40,
    "web_search": 25,
    "web_fetch": 25,
    "browser_navigate": 30,
    "browser_snapshot": 20,
    "browser_links": 10,
    "browser_form_list": 15,
    "browser_submit": 35,
    "browser_open": 60,
    "browser_click": 25,
    "browser_type": 20,
    "browser_screenshot": 40,
    "http_request": 30,
    "wikipedia": 15,
    "weather": 15,
    "rss_fetch": 20,
    "youtube_transcript": 30,
    "deep_research": 250,
    "memory_search": 10,
    "memory_save": 10,
    "note_add": 5,
    "note_list": 3,
    "note_delete": 3,
    "todo_add": 5,
    "todo_list": 3,
    "todo_done": 3,
    "email_send": 50,
    "generate_image": 500,
    "image_generate": 500,
    "page_summary": 35,
    "skill_save": 15,
    "skill_list": 5,
    "skill_run": 30,
}


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


def tool_credit_cost(name: str, is_admin: bool = False) -> int:
    if is_admin:
        return 0
    return int(TOOL_CREDIT_COSTS.get(name, 20))
