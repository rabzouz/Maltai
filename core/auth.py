"""Authentification Maltai - 100% stdlib (pas de dependance).

- Mots de passe : PBKDF2-HMAC-SHA256 (210k iterations, sel aleatoire)
- Sessions : cookie signe HMAC-SHA256 (user_id + expiration), pas de table
- Secret : settings.SESSION_SECRET ou genere et persiste dans data/secret.key
- Premier boot : compte admin cree avec mot de passe temporaire en console
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time

from core.config import DATA_DIR, settings
from core import database as db
from core.plans import normalize_plan

PBKDF2_ITERATIONS = 210_000
SESSION_TTL = 60 * 60 * 24 * 14  # 14 jours
COOKIE_NAME = "maltai_session"

_SECRET: bytes | None = None


def get_secret() -> bytes:
    """Secret de signature : .env > data/secret.key (genere si absent)."""
    global _SECRET
    if _SECRET is not None:
        return _SECRET
    if settings.SESSION_SECRET:
        _SECRET = settings.SESSION_SECRET.encode()
        return _SECRET
    key_file = DATA_DIR / "secret.key"
    if key_file.exists():
        _SECRET = key_file.read_bytes().strip()
    else:
        _SECRET = secrets.token_hex(32).encode()
        key_file.write_bytes(_SECRET)
        try:
            os.chmod(key_file, 0o600)
        except OSError:
            pass
    return _SECRET


# --- Mots de passe --------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2${PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt_hex, dk_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iters)
        )
        return hmac.compare_digest(dk.hex(), dk_hex)
    except (ValueError, AttributeError):
        return False


# --- Jetons de session (cookie signe) -------------------------------------

def _sign(payload: bytes) -> str:
    sig = hmac.new(get_secret(), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).decode().rstrip("=")


def create_token(user_id: str) -> str:
    expiry = int(time.time()) + SESSION_TTL
    payload = f"{user_id}.{expiry}".encode()
    body = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    return f"{body}.{_sign(payload)}"


def verify_token(token: str) -> str | None:
    """Retourne user_id si le jeton est valide et non expire, sinon None."""
    try:
        body, sig = token.rsplit(".", 1)
        payload = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        if not hmac.compare_digest(sig, _sign(payload)):
            return None
        user_id, expiry = payload.decode().rsplit(".", 1)
        if int(expiry) < time.time():
            return None
        return user_id
    except (ValueError, UnicodeDecodeError):
        return None


# --- Utilisateurs ---------------------------------------------------------

def _is_configured_admin_username(username: str | None) -> bool:
    return (username or "").strip() == settings.ADMIN_USER


def _with_effective_admin(user: dict | None) -> dict | None:
    if user and _is_configured_admin_username(user.get("username")):
        user = dict(user)
        user["is_admin"] = 1
        user["plan"] = "admin"
    return user


def get_user(user_id: str) -> dict | None:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return _with_effective_admin(dict(row) if row else None)
    finally:
        conn.close()


def get_user_by_username(username: str) -> dict | None:
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE username=?", (username,)
        ).fetchone()
        return _with_effective_admin(dict(row) if row else None)
    finally:
        conn.close()


def create_user(username: str, password: str, is_admin: bool = False, plan: str = "basic") -> dict:
    uid = db.new_id()
    effective_plan = normalize_plan(plan, is_admin)
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO users (id, username, password_hash, is_admin, plan, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (uid, username, hash_password(password), int(is_admin), effective_plan, db.now()),
        )
        conn.commit()
    finally:
        conn.close()
    return {"id": uid, "username": username, "is_admin": is_admin, "plan": effective_plan}


def change_password(user_id: str, new_password: str) -> None:
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE users SET password_hash=? WHERE id=?",
            (hash_password(new_password), user_id),
        )
        conn.commit()
    finally:
        conn.close()


def count_users() -> int:
    conn = db.connect()
    try:
        return conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    finally:
        conn.close()


def ensure_configured_admin() -> bool:
    """Garantit que MALTAI_ADMIN_USER garde les droits admin si le compte existe."""
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT id, is_admin, plan FROM users WHERE username=?",
            (settings.ADMIN_USER,),
        ).fetchone()
        if not row:
            return False
        if not row["is_admin"] or row["plan"] != "admin":
            conn.execute(
                "UPDATE users SET is_admin=1, plan='admin' WHERE id=?",
                (row["id"],),
            )
            conn.commit()
            print(
                f"\n[MALTAI] Compte admin '{settings.ADMIN_USER}' promu administrateur.\n",
                flush=True,
            )
        return True
    finally:
        conn.close()


def seed_admin() -> None:
    """Au premier boot : cree l'admin. Mot de passe depuis l'env si fourni,
    sinon genere et affiche en console."""
    if ensure_configured_admin():
        return
    if count_users() > 0:
        if settings.ADMIN_PASSWORD:
            create_user(settings.ADMIN_USER, settings.ADMIN_PASSWORD, is_admin=True)
            print(
                f"\n[MALTAI] Compte admin '{settings.ADMIN_USER}' cree "
                "avec le mot de passe fourni (MALTAI_ADMIN_PASSWORD).\n",
                flush=True,
            )
        return
    if settings.ADMIN_PASSWORD:
        create_user(settings.ADMIN_USER, settings.ADMIN_PASSWORD, is_admin=True)
        print(
            f"\n[MALTAI] Compte admin '{settings.ADMIN_USER}' cree "
            "avec le mot de passe fourni (MALTAI_ADMIN_PASSWORD).\n",
            flush=True,
        )
        return
    temp_password = secrets.token_urlsafe(12)
    create_user(settings.ADMIN_USER, temp_password, is_admin=True)
    banner = (
        "\n" + "=" * 62 + "\n"
        f"  MALTAI - Compte admin cree : {settings.ADMIN_USER}\n"
        f"  Mot de passe temporaire   : {temp_password}\n"
        "  Change-le dans Reglages > Compte apres connexion.\n"
        + "=" * 62 + "\n"
    )
    print(banner, flush=True)
