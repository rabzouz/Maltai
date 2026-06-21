"""Routes uploads : fichiers joints au chat + explorateur du workspace agent.

- PDF : texte extrait (pypdf) et injecte dans le contexte du modele.
- Texte/code/CSV/MD/JSON : contenu lu directement.
- Images (png/jpg/webp/gif) : conservees telles quelles, envoyees en base64
  aux modeles vision (format OpenAI image_url).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from core.config import DATA_DIR, settings
from core import database as db
from src.tools import WORKSPACE

router = APIRouter(prefix="/api", tags=["uploads"])
download_router = APIRouter(tags=["workspace_downloads"])

UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

TEXT_EXTENSIONS = {
    ".txt", ".md", ".csv", ".json", ".py", ".js", ".ts", ".html", ".css",
    ".xml", ".yaml", ".yml", ".sh", ".sql", ".log", ".kt", ".java", ".dart",
}
IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
MAX_EXTRACT_CHARS = 30_000


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", name)[:120] or "fichier"


def _extract_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        parts = []
        for page in reader.pages[:100]:
            parts.append(page.extract_text() or "")
        return "\n".join(parts)
    except Exception as e:  # pdf corrompu, chiffre...
        return f"[extraction PDF impossible : {e}]"


@router.post("/upload")
async def upload(file: UploadFile):
    raw = await file.read()
    if len(raw) > settings.CHAT_UPLOAD_MAX_BYTES:
        raise HTTPException(413, f"Fichier trop volumineux (max {settings.CHAT_UPLOAD_MAX_BYTES // (1024*1024)} Mo)")

    uid_name = db.new_id() + "_" + _safe_name(file.filename or "fichier")
    path = UPLOAD_DIR / uid_name
    path.write_bytes(raw)

    mime = file.content_type or ""
    ext = Path(file.filename or "").suffix.lower()
    text = ""
    kind = "binaire"
    if mime == "application/pdf" or ext == ".pdf":
        text = _extract_pdf(path)
        kind = "pdf"
    elif mime in IMAGE_MIMES:
        kind = "image"
    elif ext in TEXT_EXTENSIONS or mime.startswith("text/"):
        text = raw.decode("utf-8", errors="replace")
        kind = "texte"

    if len(text) > MAX_EXTRACT_CHARS:
        text = text[:MAX_EXTRACT_CHARS] + "\n…[tronque]"

    up = db.add_upload(file.filename or "fichier", mime, str(path), text)
    return {"id": up["id"], "filename": up["filename"], "kind": kind,
            "chars": len(text)}


# --- Workspace de l'agent (fichiers crees par write_file / shell) ------------

def _workspace_root_for_request(request: Request) -> Path:
    user = getattr(request.state, "user", None)
    uid = user.get("id") if user else None
    if not uid:
        return WORKSPACE
    safe = "".join(c for c in str(uid) if c.isalnum() or c in "-_")[:32] or "shared"
    return WORKSPACE / safe

@router.get("/workspace")
def workspace_list(request: Request):
    root = _workspace_root_for_request(request)
    files = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            files.append({
                "path": str(p.relative_to(root)),
                "size": p.stat().st_size,
            })
        if len(files) >= 300:
            break
    return files


@router.get("/workspace/download")
def workspace_download(path: str, request: Request):
    return _workspace_file_response(path, request)


def _workspace_file_response(path: str, request: Request):
    root = _workspace_root_for_request(request).resolve()
    target = (root / path).resolve()
    if not str(target).startswith(str(root) + os.sep) or not target.is_file():
        raise HTTPException(404, "Fichier introuvable")
    return FileResponse(str(target), filename=target.name)


@download_router.get("/exports/{path:path}")
def export_download(path: str, request: Request):
    safe_path = path.strip().lstrip("/\\")
    root = _workspace_root_for_request(request).resolve()
    exports_root = (root / "exports").resolve()
    target = (exports_root / safe_path).resolve()
    if not str(target).startswith(str(exports_root) + os.sep) or not target.is_file():
        raise HTTPException(404, "Fichier introuvable")
    return FileResponse(str(target), filename=target.name)
