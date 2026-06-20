"""Compression de contexte inspiree de Headroom.

Le but n'est pas de remplacer un vrai moteur de compression semantique, mais
de proteger la fenetre de contexte quand un outil renvoie beaucoup de JSON,
logs, HTML ou texte brut. La sortie complete reste cote outil/UI; le modele
recoit une version compacte et lisible.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

DEFAULT_MIN_CHARS = int(os.getenv("MALTAI_CONTEXT_COMPRESS_MIN_CHARS", "5000"))
DEFAULT_MAX_CHARS = int(os.getenv("MALTAI_CONTEXT_COMPRESS_MAX_CHARS", "3500"))
ENABLED = os.getenv("MALTAI_CONTEXT_COMPRESSION", "1").strip().lower() not in {"0", "false", "no", "off"}

IMPORTANT_RE = re.compile(
    r"\b(error|erreur|exception|failed|failure|fatal|traceback|warning|warn|denied|timeout|"
    r"status|url|title|titre|description|fields|metadata|result|summary|resume|prix|price|"
    r"email|telephone|phone|address|adresse|commit|version|model|token|credit)\b",
    re.I,
)


def _clip(text: str, limit: int) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    if limit <= 160:
        return text[:limit]
    head = max(80, limit // 2)
    tail = max(60, limit - head - 64)
    return text[:head].rstrip() + f"\n...[{len(text) - head - tail} chars coupes]...\n" + text[-tail:].lstrip()


def _compact_value(value: Any, depth: int = 0, limit: int = 12) -> Any:
    if depth >= 4:
        if isinstance(value, (dict, list)):
            return f"...{type(value).__name__}..."
        return _clip(str(value), 220)
    if isinstance(value, dict):
        items = list(value.items())
        compact: dict[str, Any] = {}
        priority = {
            "url", "status", "content_type", "title", "name", "description", "description_seo",
            "error", "message", "fields", "metadata", "headings", "links", "images", "json_ld",
            "path", "download_url", "version", "commit", "branch",
        }
        ordered = sorted(items, key=lambda kv: (0 if str(kv[0]).lower() in priority else 1, str(kv[0])))
        for key, item in ordered[:limit]:
            compact[str(key)] = _compact_value(item, depth + 1, limit=8)
        if len(items) > limit:
            compact["_truncated_keys"] = len(items) - limit
        return compact
    if isinstance(value, list):
        head = [_compact_value(item, depth + 1, limit=8) for item in value[:limit]]
        if len(value) > limit:
            head.append({"_truncated_items": len(value) - limit})
        return head
    if isinstance(value, str):
        return _clip(value, 700 if depth < 2 else 260)
    return value


def _try_json(text: str) -> tuple[bool, str]:
    try:
        data = json.loads(text)
    except Exception:
        return False, ""
    compact = _compact_value(data)
    return True, json.dumps(compact, ensure_ascii=False, indent=2)


def _compress_lines(text: str, max_chars: int, *, log_mode: bool = False) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    if not lines:
        return ""

    first = lines[:18 if log_mode else 14]
    last = lines[-35 if log_mode else -18:]
    important = [line for line in lines if IMPORTANT_RE.search(line)]

    blocks: list[str] = []
    blocks.append("Debut:\n" + "\n".join(first))
    if important:
        blocks.append("Lignes importantes:\n" + "\n".join(important[:45]))
    if len(lines) > len(first):
        blocks.append("Fin:\n" + "\n".join(last))

    compact = "\n\n".join(blocks)
    return _clip(compact, max_chars)


def detect_mode(text: str, requested: str = "auto") -> str:
    requested = (requested or "auto").strip().lower()
    if requested in {"json", "log", "text", "code"}:
        return requested
    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        return "json"
    if re.search(r"(traceback|exception|error|warning|exit \d+|\[[a-z]+\])", text[:2000], re.I):
        return "log"
    if re.search(r"\b(def|class|function|import|from|const|let|var|async|return)\b", text[:2000]):
        return "code"
    return "text"


def compress_text(text: str, *, mode: str = "auto", max_chars: int = DEFAULT_MAX_CHARS) -> dict[str, Any]:
    original = str(text or "")
    max_chars = max(700, min(int(max_chars or DEFAULT_MAX_CHARS), 20000))
    chosen = detect_mode(original, mode)

    if chosen == "json":
        ok, compact = _try_json(original)
        if not ok:
            compact = _compress_lines(original, max_chars, log_mode=False)
            chosen = "text"
    elif chosen == "log":
        compact = _compress_lines(original, max_chars, log_mode=True)
    elif chosen == "code":
        compact = _compress_lines(original, max_chars, log_mode=False)
    else:
        compact = _compress_lines(original, max_chars, log_mode=False)

    compact = _clip(compact, max_chars)
    saved = max(0, len(original) - len(compact))
    return {
        "mode": chosen,
        "original_chars": len(original),
        "compressed_chars": len(compact),
        "saved_chars": saved,
        "ratio": round((len(compact) / len(original)), 3) if original else 1,
        "content": compact,
    }


def format_compressed(result: dict[str, Any]) -> str:
    return (
        "[Maltai Context Compress]\n"
        f"mode={result['mode']} | chars={result['original_chars']} -> {result['compressed_chars']} "
        f"| economise={result['saved_chars']}\n"
        "La sortie complete reste disponible dans le resultat outil / fichier exporte si fourni.\n\n"
        f"{result['content']}"
    )


def compress_for_agent(tool_name: str, text: str) -> str:
    if not ENABLED:
        return text
    raw = str(text or "")
    if len(raw) < DEFAULT_MIN_CHARS:
        return raw
    result = compress_text(raw, mode="auto", max_chars=DEFAULT_MAX_CHARS)
    if result["compressed_chars"] >= result["original_chars"]:
        return raw
    return format_compressed(result)
