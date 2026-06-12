"""Notes & taches — CRUD simple pour l'UI (table notes, partagee avec l'agent)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core import database as db

router = APIRouter(prefix="/api/notes", tags=["notes"])


def _uid(request: Request) -> str | None:
    u = getattr(request.state, "user", None)
    return u["id"] if u else None


class NoteIn(BaseModel):
    content: str
    kind: str = "note"  # note | todo


@router.get("")
def list_notes(request: Request, kind: str = "note"):
    if kind not in ("note", "todo"):
        raise HTTPException(400, "kind invalide")
    uid = _uid(request)
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT id, kind, content, done, created_at FROM notes "
            "WHERE kind=? AND (user_id IS ? OR user_id=?) "
            "ORDER BY done, created_at DESC LIMIT 200",
            (kind, uid, uid),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("")
def add_note(request: Request, body: NoteIn):
    content = body.content.strip()
    if not content:
        raise HTTPException(400, "Contenu vide")
    if body.kind not in ("note", "todo"):
        raise HTTPException(400, "kind invalide")
    conn = db.connect()
    try:
        nid = db.new_id()
        conn.execute(
            "INSERT INTO notes (id, user_id, kind, content, done, created_at) "
            "VALUES (?, ?, ?, ?, 0, ?)",
            (nid, _uid(request), body.kind, content, db.now()),
        )
        conn.commit()
        return {"id": nid}
    finally:
        conn.close()


@router.patch("/{note_id}/toggle")
def toggle_note(request: Request, note_id: str):
    uid = _uid(request)
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM notes WHERE id=? AND (user_id IS ? OR user_id=?)",
            (note_id, uid, uid),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Introuvable")
        conn.execute("UPDATE notes SET done=? WHERE id=?", (0 if row["done"] else 1, note_id))
        conn.commit()
        return {"done": 0 if row["done"] else 1}
    finally:
        conn.close()


@router.delete("/{note_id}")
def delete_note(request: Request, note_id: str):
    uid = _uid(request)
    conn = db.connect()
    try:
        cur = conn.execute(
            "DELETE FROM notes WHERE id=? AND (user_id IS ? OR user_id=?)",
            (note_id, uid, uid),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "Introuvable")
        return {"ok": True}
    finally:
        conn.close()
