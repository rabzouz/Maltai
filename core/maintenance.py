"""Taches de maintenance en arriere-plan.

- Sauvegarde quotidienne de la base SQLite (rotation 7 jours) dans data/backups.
- Reconciliation Stripe quotidienne : retrograde les abonnements Premium
  annules/impayes et recredite les renouvellements de periode.

Lance par le lifespan de app.py. Chaque cycle est best-effort : une erreur
est loggee et n'arrete jamais la boucle.
"""
from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path

import httpx

from core.config import DATA_DIR, settings
from core import database as db

BACKUP_DIR = DATA_DIR / "backups"
BACKUP_KEEP = 7          # nombre de sauvegardes conservees
INTERVAL_S = 24 * 3600   # un cycle par jour
FIRST_RUN_DELAY_S = 60   # laisse l'app finir de demarrer


# --- Sauvegarde SQLite ------------------------------------------------------

def run_backup() -> Path | None:
    """Copie coherente de la base via l'API backup de SQLite (compatible WAL)."""
    src_path = settings.DATABASE_URL
    if src_path.startswith("sqlite:///"):
        src_path = src_path[len("sqlite:///"):]
    if not Path(src_path).exists():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = BACKUP_DIR / f"app-{stamp}.db"
    src = sqlite3.connect(src_path)
    try:
        out = sqlite3.connect(str(dest))
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()
    # Rotation : garde les BACKUP_KEEP plus recentes.
    backups = sorted(BACKUP_DIR.glob("app-*.db"))
    for old in backups[:-BACKUP_KEEP]:
        try:
            old.unlink()
        except OSError:
            pass
    return dest


# --- Reconciliation Stripe --------------------------------------------------

async def _stripe_get(client: httpx.AsyncClient, path: str) -> dict | None:
    r = await client.get(
        f"https://api.stripe.com{path}",
        auth=(settings.STRIPE_SECRET_KEY, ""),
    )
    if r.status_code >= 400:
        return None
    return r.json()


async def reconcile_stripe() -> dict:
    """Pour chaque utilisateur Premium : verifie l'etat reel de l'abonnement.

    - Abonnement annule / impaye / introuvable -> retour au plan basic.
    - Nouvelle periode de facturation -> credits mensuels reappliques.
    Les achats one-shot (packs de credits) ne sont pas concernes.
    """
    result = {"checked": 0, "downgraded": 0, "recredited": 0}
    if not settings.STRIPE_SECRET_KEY:
        return result

    premium_users = [u for u in db.list_users()
                     if u.get("plan") == "premium" and not u.get("is_admin")]
    if not premium_users:
        return result

    async with httpx.AsyncClient(timeout=25) as client:
        for user in premium_users:
            result["checked"] += 1
            events = db.list_billing_events_for_user(user["id"])
            sub_events = [e for e in events if e.get("plan") == "premium"]
            if not sub_events:
                continue  # premium accorde manuellement par l'admin : on ne touche pas
            last = sub_events[-1]
            session = await _stripe_get(client, f"/v1/checkout/sessions/{last['session_id']}")
            sub_id = (session or {}).get("subscription")
            if not sub_id:
                continue
            sub = await _stripe_get(client, f"/v1/subscriptions/{sub_id}")
            status = (sub or {}).get("status", "")
            if status not in {"active", "trialing", "past_due"}:
                db.set_user_plan(user["id"], "basic")
                result["downgraded"] += 1
                continue
            # Renouvellement : nouvelle periode -> credits du plan reappliques.
            period_start = int(sub.get("current_period_start") or 0)
            kv_key = f"stripe_period:{user['id']}"
            if period_start and period_start != int(db.kv_get(kv_key, 0) or 0):
                credits = (settings.PREMIUM_YEARLY_CREDITS
                           if last.get("offer") == "premium_yearly"
                           else settings.PREMIUM_MONTHLY_CREDITS)
                db.set_user_credits(user["id"], credits, reason="stripe:renewal")
                db.kv_set(kv_key, period_start)
                result["recredited"] += 1
    return result


# --- Boucle de fond ---------------------------------------------------------

async def maintenance_loop() -> None:
    await asyncio.sleep(FIRST_RUN_DELAY_S)
    while True:
        try:
            dest = await asyncio.to_thread(run_backup)
            if dest:
                print(f"[MALTAI] Sauvegarde base : {dest.name}", flush=True)
        except Exception as e:
            print(f"[MALTAI] Echec sauvegarde : {e}", flush=True)
        try:
            r = await reconcile_stripe()
            if r["checked"]:
                print(f"[MALTAI] Reconciliation Stripe : {r}", flush=True)
        except Exception as e:
            print(f"[MALTAI] Echec reconciliation Stripe : {e}", flush=True)
        await asyncio.sleep(INTERVAL_S)
