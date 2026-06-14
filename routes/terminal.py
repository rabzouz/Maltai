"""Terminal admin integre."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.config import BASE_DIR, settings
from src.tools import WORKSPACE

router = APIRouter(prefix="/api/terminal", tags=["terminal"])

MAX_OUTPUT = 20000
MAX_FILE_BYTES = 500_000
TIMEOUT = 30


class TerminalIn(BaseModel):
    command: str


class FileIn(BaseModel):
    path: str
    content: str


class PatchIn(BaseModel):
    path: str
    old_str: str = ""
    new_str: str


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
        "process": "ps -eo pid,ppid,stat,comm,args --sort=-pid | head -40",
        "models": "curl -fsS ${OLLAMA_BASE_URL:-http://127.0.0.1:11434}/api/tags",
    }
    return aliases.get(command.strip(), command)


def _safe_workspace_path(path: str) -> Path:
    rel = (path or ".").strip().lstrip("/\\")
    target = (WORKSPACE / rel).resolve()
    root = WORKSPACE.resolve()
    if target != root and not str(target).startswith(str(root) + os.sep):
        raise HTTPException(400, "Chemin hors workspace refuse")
    return target


def _workspace_rel(path: Path) -> str:
    return str(path.relative_to(WORKSPACE.resolve())).replace("\\", "/")


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


@router.get("/files")
def list_files(request: Request, path: str = "."):
    _require_admin(request)
    target = _safe_workspace_path(path)
    if not target.exists():
        raise HTTPException(404, "Dossier introuvable")
    if target.is_file():
        target = target.parent
    rows = []
    for p in sorted(target.iterdir(), key=lambda item: (item.is_file(), item.name.lower())):
        try:
            stat = p.stat()
        except OSError:
            continue
        rows.append({
            "name": p.name,
            "path": _workspace_rel(p.resolve()),
            "type": "dir" if p.is_dir() else "file",
            "size": stat.st_size,
        })
        if len(rows) >= 300:
            break
    parent = ""
    if target.resolve() != WORKSPACE.resolve():
        parent = _workspace_rel(target.parent)
    return {
        "path": _workspace_rel(target),
        "parent": parent,
        "items": rows,
    }


@router.get("/file")
def read_file(request: Request, path: str):
    _require_admin(request)
    target = _safe_workspace_path(path)
    if not target.is_file():
        raise HTTPException(404, "Fichier introuvable")
    size = target.stat().st_size
    if size > MAX_FILE_BYTES:
        raise HTTPException(413, f"Fichier trop volumineux ({size} octets)")
    return {
        "path": _workspace_rel(target),
        "size": size,
        "content": target.read_text(encoding="utf-8", errors="replace"),
    }


@router.post("/file")
def write_file(body: FileIn, request: Request):
    _require_admin(request)
    target = _safe_workspace_path(body.path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body.content, encoding="utf-8")
    return {
        "ok": True,
        "path": _workspace_rel(target),
        "size": target.stat().st_size,
    }


@router.post("/patch")
def patch_file(body: PatchIn, request: Request):
    _require_admin(request)
    target = _safe_workspace_path(body.path)
    if not target.is_file():
        raise HTTPException(404, "Fichier introuvable")
    content = target.read_text(encoding="utf-8", errors="replace")
    if body.old_str:
        if body.old_str not in content:
            raise HTTPException(400, "old_str introuvable")
        content = content.replace(body.old_str, body.new_str, 1)
    else:
        content = body.new_str
    target.write_text(content, encoding="utf-8")
    return {
        "ok": True,
        "path": _workspace_rel(target),
        "size": target.stat().st_size,
    }
