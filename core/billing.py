"""Credit and token accounting.

Maltai uses estimated token credits so it works with Ollama and OpenAI-compatible
providers even when streamed usage metadata is unavailable.
"""
from __future__ import annotations

from typing import Any

from core import database as db


def _text_tokens(text: str) -> int:
    return max(1, (len(text or "") + 3) // 4)


def _content_tokens(content: Any) -> int:
    if isinstance(content, str):
        return _text_tokens(content)
    if isinstance(content, list):
        total = 0
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    total += _text_tokens(str(item.get("text", "")))
                elif item.get("type") == "image_url":
                    total += 1000
                else:
                    total += _text_tokens(str(item))
            else:
                total += _text_tokens(str(item))
        return max(1, total)
    return _text_tokens(str(content))


def estimate_messages_tokens(messages: list[dict]) -> int:
    total = 0
    for msg in messages:
        total += 4
        total += _text_tokens(str(msg.get("role", "")))
        total += _content_tokens(msg.get("content", ""))
    return max(1, total)


def estimate_text_tokens(text: str) -> int:
    return _text_tokens(text)


def can_start_request(user: dict | None) -> bool:
    if not user or user.get("is_admin"):
        return True
    return int(user.get("credit_balance") or 0) > 0


def charge_chat(
    user: dict | None,
    session_id: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> dict:
    if not user or user.get("is_admin"):
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "credits_spent": 0,
            "balance": None,
        }

    total = max(1, input_tokens + output_tokens)
    spent, balance = db.spend_user_credits(
        user["id"],
        total,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reason="chat",
        meta={"session_id": session_id, "model": model},
    )
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total,
        "credits_spent": spent,
        "balance": balance,
    }
