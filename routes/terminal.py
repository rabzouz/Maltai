"""Terminal admin integre."""
from __future__ import annotations

import asyncio
import os
import secrets
import time
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


class ProcessStartIn(BaseModel):
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
        "process": (
            "python - <<'PY'\n"
            "import os\n"
            "for pid in sorted([p for p in os.listdir('/proc') if p.isdigit()], key=int, reverse=True)[:40]:\n"
            "    try:\n"
            "        cmd = open(f'/proc/{pid}/cmdline','rb').read().replace(b'\\0', b' ').decode(errors='replace').strip()\n"
            "        stat = open(f'/proc/{pid}/stat').read().split()\n"
            "        ppid = stat[3] if len(stat) > 3 else '?'\n"
            "        state = stat[2] if len(stat) > 2 else '?'\n"
            "    except Exception:\n"
            "        continue\n"
            "    print(f'{pid:>6} {ppid:>6} {state:>2} {cmd[:160]}')\n"
            "PY"
        ),
        "kill": (
            "echo \"Utilise l'onglet Process puis le bouton Kill sur le process selectionne.\""
        ),
        "models": "curl -fsS ${OLLAMA_BASE_URL:-http://127.0.0.1:11434}/api/tags",
    }
    key = command.strip()
    return aliases.get(key.lower(), command)


def _safe_workspace_path(path: str) -> Path:
    rel = (path or ".").strip().lstrip("/\\")
    target = (WORKSPACE / rel).resolve()
    root = WORKSPACE.resolve()
    if target != root and not str(target).startswith(str(root) + os.sep):
        raise HTTPException(400, "Chemin hors workspace refuse")
    return target


def _workspace_rel(path: Path) -> str:
    return str(path.relative_to(WORKSPACE.resolve())).replace("\\", "/")


PROCESSES: dict[str, dict] = {}


def _append_process_output(session_id: str, text: str) -> None:
    item = PROCESSES.get(session_id)
    if not item:
        return
    output = (item.get("output") or "") + text
    if len(output) > MAX_OUTPUT:
        output = output[-MAX_OUTPUT:]
    item["output"] = output
    item["updated_at"] = time.time()


async def _watch_process(session_id: str) -> None:
    item = PROCESSES.get(session_id)
    if not item:
        return
    proc = item["proc"]
    try:
        while True:
            chunk = await proc.stdout.readline()
            if not chunk:
                break
            _append_process_output(session_id, chunk.decode(errors="replace"))
        await proc.wait()
    except Exception as e:
        _append_process_output(session_id, f"\n[watch error] {e}\n")
    finally:
        item = PROCESSES.get(session_id)
        if item:
            item["exit_code"] = proc.returncode
            if item.get("status") != "killed":
                item["status"] = "exited"
            item["updated_at"] = time.time()


def _public_process(item: dict, include_output: bool = False) -> dict:
    proc = item.get("proc")
    status = item.get("status")
    if proc and proc.returncode is not None and status == "running":
        status = "exited"
    data = {
        "id": item["id"],
        "command": item["command"],
        "expanded": item.get("expanded"),
        "pid": getattr(proc, "pid", None),
        "status": status,
        "exit_code": item.get("exit_code"),
        "created_at": item["created_at"],
        "updated_at": item.get("updated_at", item["created_at"]),
    }
    if include_output:
        data["output"] = item.get("output", "")
    return data


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


@router.post("/process/start")
async def start_process(body: ProcessStartIn, request: Request):
    _require_admin(request)
    command = body.command.strip()
    if not command:
        raise HTTPException(400, "Commande vide")
    expanded = _expand_alias(command)
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    session_id = "proc_" + secrets.token_urlsafe(8)
    try:
        proc = await asyncio.create_subprocess_shell(
            expanded,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(BASE_DIR),
            env=env,
        )
    except OSError as e:
        raise HTTPException(500, f"Erreur process : {e}")

    now = time.time()
    PROCESSES[session_id] = {
        "id": session_id,
        "command": command,
        "expanded": expanded,
        "proc": proc,
        "status": "running",
        "exit_code": None,
        "output": "",
        "created_at": now,
        "updated_at": now,
    }
    asyncio.create_task(_watch_process(session_id))
    return _public_process(PROCESSES[session_id], include_output=True)


@router.get("/process")
def list_processes(request: Request):
    _require_admin(request)
    rows = sorted(PROCESSES.values(), key=lambda item: item["created_at"], reverse=True)
    return {"processes": [_public_process(item) for item in rows[:50]]}


@router.get("/process/{session_id}")
def get_process(session_id: str, request: Request):
    _require_admin(request)
    item = PROCESSES.get(session_id)
    if not item:
        raise HTTPException(404, "Process introuvable")
    return _public_process(item, include_output=True)


@router.delete("/process/{session_id}")
async def kill_process(session_id: str, request: Request):
    _require_admin(request)
    item = PROCESSES.get(session_id)
    if not item:
        raise HTTPException(404, "Process introuvable")
    proc = item["proc"]
    if proc.returncode is None:
        proc.kill()
        await proc.wait()
        item["status"] = "killed"
        item["exit_code"] = proc.returncode
        item["updated_at"] = time.time()
        _append_process_output(session_id, "\n[killed]\n")
    return _public_process(item, include_output=True)


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
