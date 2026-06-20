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
    plan          TEXT NOT NULL DEFAULT 'basic',
    credit_balance INTEGER NOT NULL DEFAULT 100000,
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
    pinned     INTEGER NOT NULL DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS notes (
    id         TEXT PRIMARY KEY,
    user_id    TEXT,
    kind       TEXT NOT NULL DEFAULT 'note',
    content    TEXT NOT NULL,
    done       INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(user_id);

CREATE TABLE IF NOT EXISTS skills (
    id         TEXT PRIMARY KEY,
    user_id    TEXT,
    name       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    body       TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_skills_name ON skills(user_id, name);

CREATE TABLE IF NOT EXISTS credit_ledger (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    delta         INTEGER NOT NULL,
    balance_after INTEGER NOT NULL,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    reason        TEXT NOT NULL DEFAULT '',
    meta          TEXT NOT NULL DEFAULT '{}',
    created_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_credit_ledger_user ON credit_ledger(user_id, created_at);

CREATE TABLE IF NOT EXISTS billing_events (
    id          TEXT PRIMARY KEY,
    session_id  TEXT UNIQUE NOT NULL,
    user_id     TEXT NOT NULL,
    offer       TEXT NOT NULL,
    plan        TEXT NOT NULL DEFAULT '',
    credits     INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_billing_events_user ON billing_events(user_id, created_at);

-- FTS5 : recherche plein texte sur les messages
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    session_id UNINDEXED,
    role UNINDEXED,
    content_rowid='rowid',
    tokenize='unicode61'
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
    user_cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    if "plan" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN plan TEXT NOT NULL DEFAULT 'basic'")
    if "credit_balance" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN credit_balance INTEGER NOT NULL DEFAULT 100000")
    conn.execute("UPDATE users SET plan='admin' WHERE is_admin=1")
    memory_cols = {r["name"] for r in conn.execute("PRAGMA table_info(memories)")}
    if "pinned" not in memory_cols:
        conn.execute("ALTER TABLE memories ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")


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


# --- Users / subscription plans ------------------------------------------

def list_users() -> list[dict[str, Any]]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT id, username, is_admin, plan, credit_balance, created_at FROM users ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def set_user_plan(user_id: str, plan: str) -> bool:
    conn = connect()
    try:
        cur = conn.execute("UPDATE users SET plan=? WHERE id=? AND is_admin=0", (plan, user_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def set_user_credits(user_id: str, credits: int, reason: str = "admin_set") -> int:
    credits = max(0, int(credits))
    conn = connect()
    try:
        row = conn.execute(
            "SELECT credit_balance FROM users WHERE id=? AND is_admin=0", (user_id,)
        ).fetchone()
        if not row:
            return 0
        delta = credits - int(row["credit_balance"])
        conn.execute("UPDATE users SET credit_balance=? WHERE id=? AND is_admin=0", (credits, user_id))
        conn.execute(
            "INSERT INTO credit_ledger (id, user_id, delta, balance_after, reason, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (new_id(), user_id, delta, credits, reason, now()),
        )
        conn.commit()
        return credits
    finally:
        conn.close()


def add_user_credits(user_id: str, credits: int, reason: str = "admin_add") -> int:
    credits = int(credits)
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT credit_balance FROM users WHERE id=? AND is_admin=0", (user_id,)
        ).fetchone()
        if not row:
            conn.rollback()
            return 0
        balance = max(0, int(row["credit_balance"]) + credits)
        conn.execute("UPDATE users SET credit_balance=? WHERE id=?", (balance, user_id))
        conn.execute(
            "INSERT INTO credit_ledger (id, user_id, delta, balance_after, reason, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (new_id(), user_id, credits, balance, reason, now()),
        )
        conn.commit()
        return balance
    finally:
        conn.close()


def spend_user_credits(
    user_id: str,
    credits: int,
    input_tokens: int = 0,
    output_tokens: int = 0,
    reason: str = "chat",
    meta: dict[str, Any] | None = None,
) -> tuple[int, int]:
    requested = max(0, int(credits))
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT credit_balance FROM users WHERE id=? AND is_admin=0", (user_id,)
        ).fetchone()
        if not row:
            conn.rollback()
            return 0, 0
        current = max(0, int(row["credit_balance"]))
        spent = min(current, requested)
        balance = current - spent
        conn.execute("UPDATE users SET credit_balance=? WHERE id=?", (balance, user_id))
        conn.execute(
            "INSERT INTO credit_ledger "
            "(id, user_id, delta, balance_after, input_tokens, output_tokens, reason, meta, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                new_id(), user_id, -spent, balance, int(input_tokens), int(output_tokens),
                reason, json.dumps(meta or {}, ensure_ascii=False), now(),
            ),
        )
        conn.commit()
        return spent, balance
    finally:
        conn.close()


def list_credit_ledger(user_id: str, limit: int = 20) -> list[dict[str, Any]]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM credit_ledger WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (user_id, max(1, min(int(limit), 100))),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_billing_event(session_id: str) -> dict[str, Any] | None:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM billing_events WHERE session_id=?", (session_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def record_billing_event(session_id: str, user_id: str, offer: str, plan: str = "", credits: int = 0) -> bool:
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO billing_events (id, session_id, user_id, offer, plan, credits, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (new_id(), session_id, user_id, offer, plan, int(credits), now()),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
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
        cur = conn.execute(
            "INSERT INTO messages (id, session_id, role, content, created_at) VALUES (?,?,?,?,?)",
            (mid, sid, role, content, ts),
        )
        rowid = cur.lastrowid
        # Index FTS5 (best-effort)
        try:
            conn.execute(
                "INSERT INTO messages_fts(rowid, content, session_id, role) VALUES (?,?,?,?)",
                (rowid, content, sid, role),
            )
        except Exception:
            pass
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
    """Retourne (id, session_id, role, content, embedding, dim, pinned) pour cet utilisateur."""
    conn = connect()
    try:
        if user_id is None:
            rows = conn.execute(
                "SELECT id, session_id, role, content, embedding, dim, pinned "
                "FROM memories WHERE user_id IS NULL"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, session_id, role, content, embedding, dim, pinned "
                "FROM memories WHERE user_id = ?", (user_id,)
            ).fetchall()
        return [tuple(r) for r in rows]
    finally:
        conn.close()


def list_memories(
    user_id: str | None,
    limit: int = 50,
    query: str = "",
    role: str = "",
    pinned: str = "",
) -> list[dict[str, Any]]:
    conn = connect()
    try:
        lim = max(1, min(int(limit), 200))
        q = (query or "").strip().lower()
        if user_id is None:
            sql = "SELECT id, session_id, role, content, dim, pinned, created_at FROM memories WHERE user_id IS NULL"
            params: list[Any] = []
        else:
            sql = "SELECT id, session_id, role, content, dim, pinned, created_at FROM memories WHERE user_id = ?"
            params = [user_id]
        if q:
            sql += " AND lower(content) LIKE ?"
            params.append(f"%{q}%")
        if role in ("user", "assistant"):
            sql += " AND role = ?"
            params.append(role)
        if pinned == "1":
            sql += " AND pinned = 1"
        sql += " ORDER BY pinned DESC, created_at DESC LIMIT ?"
        params.append(lim)
        rows = conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_memory(user_id: str | None, memory_id: str) -> bool:
    conn = connect()
    try:
        if user_id is None:
            cur = conn.execute(
                "DELETE FROM memories WHERE id=? AND user_id IS NULL",
                (memory_id,),
            )
        else:
            cur = conn.execute(
                "DELETE FROM memories WHERE id=? AND user_id=?",
                (memory_id, user_id),
            )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def set_memory_pinned(user_id: str | None, memory_id: str, pinned: bool) -> bool:
    conn = connect()
    try:
        if user_id is None:
            cur = conn.execute(
                "UPDATE memories SET pinned=? WHERE id=? AND user_id IS NULL",
                (1 if pinned else 0, memory_id),
            )
        else:
            cur = conn.execute(
                "UPDATE memories SET pinned=? WHERE id=? AND user_id=?",
                (1 if pinned else 0, memory_id, user_id),
            )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_memories_filtered(user_id: str | None, query: str = "", role: str = "") -> int:
    q = (query or "").strip().lower()
    conn = connect()
    try:
        if user_id is None:
            sql = "DELETE FROM memories WHERE user_id IS NULL"
            params: list[Any] = []
        else:
            sql = "DELETE FROM memories WHERE user_id = ?"
            params = [user_id]
        if q:
            sql += " AND lower(content) LIKE ?"
            params.append(f"%{q}%")
        if role in ("user", "assistant"):
            sql += " AND role = ?"
            params.append(role)
        sql += " AND pinned = 0"
        cur = conn.execute(sql, tuple(params))
        conn.commit()
        return cur.rowcount
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


# --- Skills ------------------------------------------------------------------

def skill_save(user_id, name, description, body):
    sid = new_id()
    ts = now()
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO skills (id, user_id, name, description, body, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(user_id, name) DO UPDATE SET description=excluded.description, "
            "body=excluded.body, updated_at=excluded.updated_at",
            (sid, user_id, name, description, body, ts, ts),
        )
        conn.commit()
    finally:
        conn.close()
    return name


def list_skills(user_id):
    conn = connect()
    try:
        if user_id is None:
            rows = conn.execute(
                "SELECT id, name, description, body, updated_at FROM skills "
                "WHERE user_id IS NULL ORDER BY name"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, name, description, body, updated_at FROM skills "
                "WHERE user_id=? ORDER BY name", (user_id,)
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_skill(user_id, name):
    conn = connect()
    try:
        if user_id is None:
            row = conn.execute(
                "SELECT * FROM skills WHERE user_id IS NULL AND name=?", (name,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM skills WHERE user_id=? AND name=?", (user_id, name)
            ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# --- FTS5 : recherche sessions -----------------------------------------------

def fts_index_message(session_id, role, content, rowid):
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO messages_fts(rowid, content, session_id, role) VALUES (?,?,?,?)",
            (rowid, content, session_id, role),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def fts_search(query, user_id, limit=10):
    conn = connect()
    try:
        if user_id is not None:
            rows = conn.execute(
                "SELECT m.session_id, m.role, "
                "snippet(messages_fts, 0, '[', ']', '...', 20) AS snippet, "
                "s.title AS session_title, s.updated_at "
                "FROM messages_fts "
                "JOIN messages m ON messages_fts.rowid = m.rowid "
                "JOIN sessions s ON m.session_id = s.id "
                "WHERE messages_fts MATCH ? AND s.user_id = ? "
                "ORDER BY rank LIMIT ?",
                (query, user_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT m.session_id, m.role, "
                "snippet(messages_fts, 0, '[', ']', '...', 20) AS snippet, "
                "s.title AS session_title, s.updated_at "
                "FROM messages_fts "
                "JOIN messages m ON messages_fts.rowid = m.rowid "
                "JOIN sessions s ON m.session_id = s.id "
                "WHERE messages_fts MATCH ? "
                "ORDER BY rank LIMIT ?",
                (query, limit),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        return [{"error": str(e)}]
    finally:
        conn.close()

