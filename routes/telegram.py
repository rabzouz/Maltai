"""Routes Telegram : configuration (UI) + webhook public (Telegram)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from core.config import settings
from src import connector, telegram

router = APIRouter(prefix="/api/telegram", tags=["telegram"])


def _require_admin(request: Request) -> None:
    if not settings.AUTH_ENABLED:
        return
    user = getattr(request.state, "user", None)
    if not user or not user.get("is_admin"):
        raise HTTPException(403, "Reserve aux administrateurs")


class TelegramConfigIn(BaseModel):
    token: str = ""
    allowed_chat_ids: list[str] = []
    agent: bool = False
    enabled: bool = False
    public_url: str = ""


@router.get("/config")
def get_config(request: Request):
    _require_admin(request)
    cfg = telegram.get_config()
    return {
        "has_token": bool(cfg.get("token")),
        "allowed_chat_ids": cfg.get("allowed_chat_ids", []),
        "agent": bool(cfg.get("agent")),
        "enabled": bool(cfg.get("enabled")),
    }


@router.post("/config")
async def set_config(body: TelegramConfigIn, request: Request):
    _require_admin(request)
    cfg = telegram.get_config()
    if body.token.strip():
        cfg["token"] = body.token.strip()
    cfg["allowed_chat_ids"] = [c.strip() for c in body.allowed_chat_ids if c.strip()]
    cfg["agent"] = body.agent
    cfg["enabled"] = body.enabled
    cfg = telegram.save_config(cfg)

    webhook_info = None
    if body.enabled and body.public_url.strip():
        if not cfg.get("token"):
            raise HTTPException(400, "Jeton requis pour activer le webhook")
        try:
            webhook_info = await telegram.set_webhook(body.public_url.strip())
        except connector.ConnectorError as e:
            raise HTTPException(502, str(e))
    elif not body.enabled and cfg.get("token"):
        try:
            await telegram.delete_webhook()
        except connector.ConnectorError:
            pass  # best-effort

    return {"ok": True, "webhook": webhook_info}


@router.post("/webhook/{secret}")
async def webhook(secret: str, request: Request):
    """Endpoint public appele par les serveurs Telegram."""
    cfg = telegram.get_config()
    expected = cfg.get("secret", "")
    header_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not expected or secret != expected or (header_secret and header_secret != expected[:64]):
        raise HTTPException(403, "Webhook non autorise")
    update = await request.json()
    try:
        await telegram.handle_update(update)
    except Exception:
        pass  # Telegram re-essaie en cas d'erreur 5xx : on absorbe pour eviter les boucles
    return Response(status_code=200)
