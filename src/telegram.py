"""Connecteur Telegram (Bot API, mode webhook).

Pourquoi webhook plutot que polling : sur Coolify, Maltai a deja un domaine
HTTPS public — Telegram pousse les messages directement, zero processus en
plus, zero latence de poll.

Securite :
- L'URL du webhook contient un secret aleatoire (genere a la config).
- Le header X-Telegram-Bot-Api-Secret-Token est aussi verifie.
- Liste blanche de chat IDs : un chat inconnu recoit son ID et une consigne,
  rien n'est traite tant qu'il n'est pas autorise dans les Reglages.

Config stockee en kv sous "telegram":
{ token, secret, allowed_chat_ids: [..], agent: bool, enabled: bool }
"""
from __future__ import annotations

import secrets as pysecrets

import os

import httpx

from core import database as db
from src import connector

# Surchargeable pour les tests (et proxys eventuels).
API_BASE = os.getenv("TELEGRAM_API_BASE", "https://api.telegram.org")

KV_KEY = "telegram"
MAX_TG_LEN = 4000  # limite Telegram ~4096


def get_config() -> dict:
    return db.kv_get(KV_KEY, {
        "token": "", "secret": "", "allowed_chat_ids": [],
        "agent": False, "enabled": False,
    })


def save_config(cfg: dict) -> dict:
    if not cfg.get("secret"):
        cfg["secret"] = pysecrets.token_urlsafe(24)
    db.kv_set(KV_KEY, cfg)
    return cfg


async def api(method: str, payload: dict) -> dict:
    cfg = get_config()
    token = cfg.get("token", "")
    if not token:
        raise connector.ConnectorError("Jeton Telegram non configure")
    url = f"{API_BASE}/bot{token}/{method}"
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            r = await client.post(url, json=payload)
        except httpx.HTTPError as e:
            raise connector.ConnectorError(f"Telegram injoignable : {e}") from e
    data = r.json()
    if not data.get("ok"):
        raise connector.ConnectorError(f"Telegram : {data.get('description', r.text[:200])}")
    return data.get("result", {})


async def set_webhook(public_url: str) -> dict:
    cfg = get_config()
    webhook_url = public_url.rstrip("/") + f"/api/telegram/webhook/{cfg['secret']}"
    return await api("setWebhook", {
        "url": webhook_url,
        "secret_token": cfg["secret"][:64],
        "allowed_updates": ["message"],
    })


async def delete_webhook() -> dict:
    return await api("deleteWebhook", {})


async def send_message(chat_id: str, text: str) -> None:
    """Envoie en Markdown ; retombe en texte brut si Telegram refuse le parse."""
    for chunk_start in range(0, max(len(text), 1), MAX_TG_LEN):
        chunk = text[chunk_start:chunk_start + MAX_TG_LEN]
        try:
            await api("sendMessage", {
                "chat_id": chat_id, "text": chunk, "parse_mode": "Markdown",
            })
        except connector.ConnectorError:
            await api("sendMessage", {"chat_id": chat_id, "text": chunk})


async def handle_update(update: dict) -> None:
    """Traite un update Telegram entrant (message texte uniquement)."""
    msg = update.get("message") or {}
    chat_id = str((msg.get("chat") or {}).get("id", ""))
    text = (msg.get("text") or "").strip()
    if not chat_id or not text:
        return

    cfg = get_config()
    if not cfg.get("enabled"):
        return

    allowed = [str(c) for c in cfg.get("allowed_chat_ids", [])]
    if chat_id not in allowed:
        await send_message(
            chat_id,
            "⛔ Chat non autorisé.\n"
            f"Ton chat ID est : `{chat_id}`\n"
            "Ajoute-le dans Maltai → Réglages → Telegram pour activer ce chat.",
        )
        return

    # Session dediee a ce chat Telegram (continuite de la conversation).
    session_id = db.telegram_get_session(chat_id)
    if not session_id or not any(s["id"] == session_id for s in db.list_sessions()):
        session = db.create_session(None, None, None)
        session_id = session["id"]
        db.rename_session(session_id, f"Telegram {chat_id}")
        db.telegram_bind_session(chat_id, session_id)

    try:
        answer = await connector.run_turn(
            session_id, text,
            use_agent=bool(cfg.get("agent")),
            is_admin=False,  # jamais d'outil shell via Telegram
        )
    except connector.ConnectorError as e:
        answer = f"⚠ {e}"
    await send_message(chat_id, answer or "…")
