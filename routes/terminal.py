"""Terminal admin integre."""
from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.config import BASE_DIR, settings

router = APIRouter(prefix="/api/terminal", tags=["terminal"])

MAX_OUTPUT = 20000
TIMEOUT = 30


class TerminalIn(BaseModel):
    command: str


def _require_admin(request: Request) -> None:
    if not settings.AUTH_ENABLED:
        return
    user = getattr(request.state, "user", None)
    if not user or not user.get("is_admin"):
        raise HTTPException(403, "Admin requis")


def _expand_alias(command: str) -> str:
    aliases = {
        "version": (
            "python -c \"from core.config import settings; "
            "print(settings.APP_NAME + ' ' + settings.APP_VERSION)\""
        ),
        "health": (
            "python -c \"from core.config import settings; "
            "print({'app': settings.APP_NAME, 'version': settings.APP_VERSION, 'status': 'ok'})\""
        ),
        "pwd": "pwd",
        "ls": "ls -la",
        "models": "curl -fsS ${OLLAMA_BASE_URL:-http://127.0.0.1:11434}/api/tags",
    }
    return aliases.get(command.strip(), command)


@router.post("/run")
async def run_terminal(body: TerminalIn, request: Request):
    _require_admin(request)
    command = body.command.strip()
    if not command:
        raise HTTPException(400, "Commande vide")
    expanded = _expand_alias(command)
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")

    try:
        proc = await asyncio.create_subprocess_shell(
            expanded,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(BASE_DIR),
            env=env,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        return {
            "command": command,
            "expanded": expanded,
            "exit_code": None,
            "output": f"Timeout ({TIMEOUT}s) - commande interrompue",
        }
    except OSError as e:
        raise HTTPException(500, f"Erreur terminal : {e}")

    text = out.decode(errors="replace")
    if len(text) > MAX_OUTPUT:
        text = text[:MAX_OUTPUT] + "\n...[sortie tronquee]"
    return {
        "command": command,
        "expanded": expanded,
        "exit_code": proc.returncode,
        "output": text,
    }
