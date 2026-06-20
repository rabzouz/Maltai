"""Stripe Checkout pour plans Premium et packs de credits."""
from __future__ import annotations

from urllib.parse import urljoin

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core import database as db
from core.config import settings

router = APIRouter(prefix="/api/billing", tags=["billing"])


class CheckoutIn(BaseModel):
    offer: str


OFFERS = {
    "premium_monthly": {
        "name": "Premium mensuel",
        "price": "9,99 EUR / mois",
        "description": "Outils agent, scraping, browser, fichiers, code sandbox et crédits inclus.",
        "mode": "subscription",
        "plan": "premium",
        "credits": settings.PREMIUM_MONTHLY_CREDITS,
        "price_setting": "STRIPE_PREMIUM_MONTHLY_PRICE_ID",
    },
    "premium_yearly": {
        "name": "Premium annuel",
        "price": "99 EUR / an",
        "description": "Premium pendant un an avec tarif réduit.",
        "mode": "subscription",
        "plan": "premium",
        "credits": settings.PREMIUM_YEARLY_CREDITS,
        "price_setting": "STRIPE_PREMIUM_YEARLY_PRICE_ID",
    },
    "credits_100k": {
        "name": "Pack 100 000 crédits",
        "price": "5 EUR",
        "description": "Recharge de crédits pour exécuter plus d'outils et d'agents.",
        "mode": "payment",
        "plan": "",
        "credits": 100_000,
        "price_setting": "STRIPE_CREDITS_100K_PRICE_ID",
    },
}


def _public_base_url(request: Request) -> str:
    configured = settings.APP_PUBLIC_URL.strip().rstrip("/")
    if configured:
        return configured
    return str(request.base_url).rstrip("/")


def _price_id(offer: dict) -> str:
    return str(getattr(settings, offer["price_setting"], "") or "").strip()


def _public_offer(key: str, offer: dict) -> dict:
    return {
        "id": key,
        "name": offer["name"],
        "price": offer["price"],
        "description": offer["description"],
        "mode": offer["mode"],
        "plan": offer["plan"],
        "credits": offer["credits"],
        "configured": bool(_price_id(offer)),
    }


async def _stripe_post(path: str, data: dict[str, str]) -> dict:
    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(503, "Stripe non configure : STRIPE_SECRET_KEY manquant")
    async with httpx.AsyncClient(timeout=25) as client:
        r = await client.post(
            urljoin("https://api.stripe.com", path),
            data=data,
            auth=(settings.STRIPE_SECRET_KEY, ""),
        )
    if r.status_code >= 400:
        detail = r.json().get("error", {}).get("message", r.text)
        raise HTTPException(502, f"Stripe : {detail}")
    return r.json()


async def _stripe_get(path: str) -> dict:
    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(503, "Stripe non configure : STRIPE_SECRET_KEY manquant")
    async with httpx.AsyncClient(timeout=25) as client:
        r = await client.get(
            urljoin("https://api.stripe.com", path),
            auth=(settings.STRIPE_SECRET_KEY, ""),
        )
    if r.status_code >= 400:
        detail = r.json().get("error", {}).get("message", r.text)
        raise HTTPException(502, f"Stripe : {detail}")
    return r.json()


@router.get("/plans")
def plans():
    return {
        "stripe_configured": bool(settings.STRIPE_SECRET_KEY),
        "offers": [_public_offer(k, v) for k, v in OFFERS.items()],
    }


@router.post("/checkout")
async def checkout(body: CheckoutIn, request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Non authentifie")
    offer = OFFERS.get(body.offer)
    if not offer:
        raise HTTPException(400, "Offre inconnue")
    price = _price_id(offer)
    if not price:
        raise HTTPException(503, f"Offre non configuree : {offer['price_setting']}")

    base = _public_base_url(request)
    session = await _stripe_post("/v1/checkout/sessions", {
        "mode": offer["mode"],
        "line_items[0][price]": price,
        "line_items[0][quantity]": "1",
        "client_reference_id": user["id"],
        "metadata[user_id]": user["id"],
        "metadata[offer]": body.offer,
        "success_url": f"{base}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{base}/billing?cancel=1",
    })
    return {"url": session.get("url"), "id": session.get("id")}


@router.post("/confirm")
async def confirm(body: dict, request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Non authentifie")
    session_id = str(body.get("session_id", "")).strip()
    if not session_id.startswith("cs_"):
        raise HTTPException(400, "Session Stripe invalide")

    existing = db.get_billing_event(session_id)
    if existing:
        return {"ok": True, "already_processed": True, "event": existing}

    session = await _stripe_get(f"/v1/checkout/sessions/{session_id}")
    meta = session.get("metadata") or {}
    if meta.get("user_id") != user["id"]:
        raise HTTPException(403, "Session Stripe rattachee a un autre utilisateur")
    if session.get("status") != "complete":
        raise HTTPException(402, "Paiement Stripe non termine")
    if session.get("payment_status") not in {"paid", "no_payment_required"}:
        raise HTTPException(402, "Paiement Stripe non valide")

    offer_key = meta.get("offer") or ""
    offer = OFFERS.get(offer_key)
    if not offer:
        raise HTTPException(400, "Offre inconnue dans la session Stripe")

    plan = offer["plan"]
    credits = int(offer["credits"])
    if plan:
        db.set_user_plan(user["id"], plan)
    if credits:
        if plan:
            db.set_user_credits(user["id"], credits, reason=f"stripe:{offer_key}")
        else:
            db.add_user_credits(user["id"], credits, reason=f"stripe:{offer_key}")
    db.record_billing_event(session_id, user["id"], offer_key, plan=plan, credits=credits)
    return {"ok": True, "plan": plan, "credits": credits}
