"""Couche base de donnees (SQLite via sqlite3 standard, zero dependance ORM).

Tables : users, providers, sessions, messages.
Tout est volontairement simple pour rester hackable.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from core.config import settings


def _db_path() -> str:
    url = settings.DATABASE_URL
    if url.startswith("sqlite:///"):
        return url[len("sqlite:///"):]
    return url


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS providers (
    id        TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    base_url  TEXT NOT NULL,
    api_key   TEXT NOT NULL DEFAULT '',
    model     TEXT NOT NULL DEFAULT '',
    embed_model TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    user_id     TEXT,
    title       TEXT NOT NULL DEFAULT 'Nouvelle discussion',
    provider_id TEXT,
    model       TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id         TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS memories (
    id         TEXT PRIMARY KEY,
    user_id    TEXT,
    session_id TEXT,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    embedding  BLOB NOT NULL,
    dim        INTEGER NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id);

CREATE TABLE IF NOT EXISTS uploads (
    id          TEXT PRIMARY KEY,
    filename    TEXT NOT NULL,
    mime        TEXT NOT NULL DEFAULT '',
    path        TEXT NOT NULL,
    text_extract TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS telegram_chats (
    chat_id    TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS mcp_servers (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    url        TEXT NOT NULL,
    auth_token TEXT NOT NULL DEFAULT '',
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);
"""


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """Migrations douces pour les bases creees avant une colonne."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(providers)")}
    if "embed_model" not in cols:
        conn.execute("ALTER TABLE providers ADD COLUMN embed_model TEXT NOT NULL DEFAULT ''")


def new_id() -> str:
    return uuid.uuid4().hex


def now() -> float:
    return time.time()


# --- Providers -----------------------------------------------------------

def list_providers() -> list[dict[str, Any]]:
    conn = connect()
    try:
        rows = conn.execute("SELECT * FROM providers ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def add_provider(
    name: str, base_url: str, api_key: str, model: str, embed_model: str = ""
) -> dict[str, Any]:
    pid = new_id()
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO providers (id, name, base_url, api_key, model, embed_model, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (pid, name, base_url, api_key, model, embed_model, now()),
        )
        conn.commit()
    finally:
        conn.close()
    return {"id": pid, "name": name, "base_url": base_url, "api_key": api_key,
            "model": model, "embed_model": embed_model}


def get_provider(pid: str) -> dict[str, Any] | None:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM providers WHERE id=?", (pid,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def delete_provider(pid: str) -> None:
    conn = connect()
    try:
        conn.execute("DELETE FROM providers WHERE id=?", (pid,))
        conn.commit()
    finally:
        conn.close()


# --- Sessions ------------------------------------------------------------

def list_sessions() -> list[dict[str, Any]]:
    conn = connect()
    try:
        rows = conn.execute("SELECT * FROM sessions ORDER BY updated_at DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def create_session(provider_id: str | None, model: str | None) -> dict[str, Any]:
    sid = new_id()
    ts = now()
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO sessions (id, title, provider_id, model, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (sid, "Nouvelle discussion", provider_id, model, ts, ts),
        )
        conn.commit()
    finally:
        conn.close()
    return {"id": sid, "title": "Nouvelle discussion", "provider_id": provider_id, "model": model}


def rename_session(sid: str, title: str) -> None:
    conn = connect()
    try:
        conn.execute("UPDATE sessions SET title=?, updated_at=? WHERE id=?", (title, now(), sid))
        conn.commit()
    finally:
        conn.close()


def touch_session(sid: str) -> None:
    conn = connect()
    try:
        conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now(), sid))
        conn.commit()
    finally:
        conn.close()


def delete_session(sid: str) -> None:
    conn = connect()
    try:
        conn.execute("DELETE FROM sessions WHERE id=?", (sid,))
        conn.commit()
    finally:
        conn.close()


# --- Messages ------------------------------------------------------------

def list_messages(sid: str) -> list[dict[str, Any]]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM messages WHERE session_id=? ORDER BY created_at", (sid,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def add_message(sid: str, role: str, content: str) -> dict[str, Any]:
    mid = new_id()
    ts = now()
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO messages (id, session_id, role, content, created_at) VALUES (?,?,?,?,?)",
            (mid, sid, role, content, ts),
        )
        conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (ts, sid))
        conn.commit()
    finally:
        conn.close()
    return {"id": mid, "session_id": sid, "role": role, "content": content, "created_at": ts}


# --- Memories (vecteurs) -------------------------------------------------

def add_memory(
    user_id: str | None, session_id: str | None, role: str,
    content: str, embedding: bytes, dim: int,
) -> str:
    mid = new_id()
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO memories (id, user_id, session_id, role, content, embedding, dim, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (mid, user_id, session_id, role, content, embedding, dim, now()),
        )
        conn.commit()
    finally:
        conn.close()
    return mid


def iter_memories(user_id: str | None):
    """Retourne (id, session_id, role, content, embedding, dim) pour cet utilisateur."""
    conn = connect()
    try:
        if user_id is None:
            rows = conn.execute(
                "SELECT id, session_id, role, content, embedding, dim "
                "FROM memories WHERE user_id IS NULL"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, session_id, role, content, embedding, dim "
                "FROM memories WHERE user_id = ?", (user_id,)
            ).fetchall()
        return [tuple(r) for r in rows]
    finally:
        conn.close()


def count_memories(user_id: str | None) -> int:
    conn = connect()
    try:
        if user_id is None:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM memories WHERE user_id IS NULL"
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM memories WHERE user_id = ?", (user_id,)
            ).fetchone()
        return row["n"]
    finally:
        conn.close()


def clear_memories(user_id: str | None) -> int:
    n = count_memories(user_id)
    conn = connect()
    try:
        if user_id is None:
            conn.execute("DELETE FROM memories WHERE user_id IS NULL")
        else:
            conn.execute("DELETE FROM memories WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()
    return n


# --- Serveurs MCP ---------------------------------------------------------

def list_mcp_servers(enabled_only: bool = False) -> list[dict[str, Any]]:
    conn = connect()
    try:
        q = "SELECT * FROM mcp_servers"
        if enabled_only:
            q += " WHERE enabled = 1"
        q += " ORDER BY created_at"
        return [dict(r) for r in conn.execute(q).fetchall()]
    finally:
        conn.close()


def add_mcp_server(name: str, url: str, auth_token: str = "") -> dict[str, Any]:
    sid = new_id()
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO mcp_servers (id, name, url, auth_token, enabled, created_at) "
            "VALUES (?,?,?,?,1,?)",
            (sid, name, url, auth_token, now()),
        )
        conn.commit()
    finally:
        conn.close()
    return {"id": sid, "name": name, "url": url, "enabled": True}


def get_mcp_server(sid: str) -> dict[str, Any] | None:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM mcp_servers WHERE id=?", (sid,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def set_mcp_enabled(sid: str, enabled: bool) -> None:
    conn = connect()
    try:
        conn.execute("UPDATE mcp_servers SET enabled=? WHERE id=?", (int(enabled), sid))
        conn.commit()
    finally:
        conn.close()


def delete_mcp_server(sid: str) -> None:
    conn = connect()
    try:
        conn.execute("DELETE FROM mcp_servers WHERE id=?", (sid,))
        conn.commit()
    finally:
        conn.close()


# --- KV (config connecteurs) ----------------------------------------------

def kv_get(key: str, default=None):
    conn = connect()
    try:
        row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        if not row:
            return default
        return json.loads(row["value"])
    finally:
        conn.close()


def kv_set(key: str, value) -> None:
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO kv (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()


# --- Telegram chats ---------------------------------------------------------

def telegram_get_session(chat_id: str) -> str | None:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT session_id FROM telegram_chats WHERE chat_id=?", (chat_id,)
        ).fetchone()
        return row["session_id"] if row else None
    finally:
        conn.close()


def telegram_bind_session(chat_id: str, session_id: str) -> None:
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO telegram_chats (chat_id, session_id, created_at) VALUES (?,?,?) "
            "ON CONFLICT(chat_id) DO UPDATE SET session_id=excluded.session_id",
            (chat_id, session_id, now()),
        )
        conn.commit()
    finally:
        conn.close()


# --- Uploads ----------------------------------------------------------------

def add_upload(filename: str, mime: str, path: str, text_extract: str) -> dict[str, Any]:
    uid = new_id()
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO uploads (id, filename, mime, path, text_extract, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (uid, filename, mime, path, text_extract, now()),
        )
        conn.commit()
    finally:
        conn.close()
    return {"id": uid, "filename": filename, "mime": mime}


def get_upload(uid: str) -> dict[str, Any] | None:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM uploads WHERE id=?", (uid,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# --- Troncature / regeneration ----------------------------------------------

def truncate_from(session_id: str, message_id: str) -> int:
    """Supprime le message vise et tout ce qui suit dans la session.
    Retourne le nombre de messages supprimes. Utilise rowid (ordre d'insertion)
    pour etre exact meme si deux messages partagent le meme timestamp."""
    conn = connect()
    try:
        row = conn.execute(
            "SELECT rowid FROM messages WHERE id=? AND session_id=?",
            (message_id, session_id),
        ).fetchone()
        if not row:
            return 0
        cur = conn.execute(
            "DELETE FROM messages WHERE session_id=? AND rowid>=?",
            (session_id, row["rowid"]),
        )
        conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now(), session_id))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def pop_last_exchange(session_id: str) -> str | None:
    """Pour regenerer : supprime les reponses assistant en fin de session puis
    le dernier message user, et retourne son contenu (a renvoyer au modele)."""
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT rowid, id, role, content FROM messages "
            "WHERE session_id=? ORDER BY rowid DESC",
            (session_id,),
        ).fetchall()
        if not rows:
            return None
        to_delete = []
        user_content = None
        for r in rows:
            if r["role"] == "assistant" and user_content is None:
                to_delete.append(r["rowid"])
                continue
            if r["role"] == "user":
                user_content = r["content"]
                to_delete.append(r["rowid"])
            break
        if user_content is None:
            return None
        qmarks = ",".join("?" * len(to_delete))
        conn.execute(
            f"DELETE FROM messages WHERE session_id=? AND rowid IN ({qmarks})",
            (session_id, *to_delete),
        )
        conn.commit()
        return user_content
    finally:
        conn.close()
