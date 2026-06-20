"""Configuration centrale de Maltai.

Tout passe par les variables d'environnement (fichier .env). Les valeurs par
defaut fonctionnent en local sans configuration. On ne touche au .env que pour
les overrides de deploiement (bind, port, auth, base de donnees...).
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Racine du projet et dossier de donnees (gitignore).
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# Charge .env s'il existe, sans ecraser un environnement deja defini.
load_dotenv(BASE_DIR / ".env", override=False)


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    APP_NAME = "Maltai"
    APP_VERSION = "1.1.0"

    # Reseau
    APP_BIND = os.getenv("APP_BIND", "127.0.0.1")
    APP_PORT = int(os.getenv("APP_PORT", "7000"))

    # Auth / sessions
    AUTH_ENABLED = _bool("AUTH_ENABLED", True)
    LOCALHOST_BYPASS = _bool("LOCALHOST_BYPASS", False)
    SECURE_COOKIES = _bool("SECURE_COOKIES", False)  # Mettre true en prod HTTPS
    REGISTRATION_ENABLED = _bool("MALTAI_REGISTRATION_ENABLED", True)
    SESSION_SECRET = os.getenv("SESSION_SECRET", "")  # genere au setup si vide
    # Avertissement si secret non configure en production
    @classmethod
    def check_security(cls):
        import secrets, warnings
        if not cls.SESSION_SECRET:
            cls.SESSION_SECRET = secrets.token_hex(32)
            warnings.warn(
                "SESSION_SECRET non configure — genere temporairement. "
                "Definissez SESSION_SECRET dans .env pour des sessions persistantes.",
                stacklevel=2
            )
    ADMIN_USER = os.getenv("MALTAI_ADMIN_USER", "admin")
    ADMIN_PASSWORD = os.getenv("MALTAI_ADMIN_PASSWORD", "")  # si vide: genere au 1er boot

    # Base de donnees
    DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'app.db'}")

    # Provider LLM par defaut (compatible OpenAI : Ollama, vLLM, llama.cpp,
    # OpenRouter, OpenAI...). Modifiable ensuite dans Reglages.
    DEFAULT_PROVIDER_NAME = os.getenv("DEFAULT_PROVIDER_NAME", "Ollama local")
    DEFAULT_BASE_URL = os.getenv("DEFAULT_BASE_URL", "http://localhost:11434/v1")
    DEFAULT_API_KEY = os.getenv("DEFAULT_API_KEY", "ollama")
    DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "llama3.1:8b")
    DEFAULT_EMBED_MODEL = os.getenv("DEFAULT_EMBED_MODEL", "nomic-embed-text")
    OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

    # Generation d'images (endpoint compatible OpenAI /v1/images/generations)
    IMAGE_API_BASE = os.getenv("IMAGE_API_BASE", "")
    IMAGE_API_KEY = os.getenv("IMAGE_API_KEY", "")
    IMAGE_MODEL = os.getenv("IMAGE_MODEL", "")

    # Memoire vectorielle
    MEMORY_ENABLED = os.getenv("MEMORY_ENABLED", "true").lower() == "true"
    MEMORY_TOP_K = int(os.getenv("MEMORY_TOP_K", "4"))
    MEMORY_MIN_SCORE = float(os.getenv("MEMORY_MIN_SCORE", "0.3"))

    # Limites d'upload (octets)
    CHAT_UPLOAD_MAX_BYTES = int(os.getenv("MALTAI_CHAT_UPLOAD_MAX_BYTES", str(10 * 1024 * 1024)))

    # Stripe Checkout (optionnel)
    APP_PUBLIC_URL = os.getenv("APP_PUBLIC_URL", "")
    STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
    STRIPE_PREMIUM_MONTHLY_PRICE_ID = os.getenv("STRIPE_PREMIUM_MONTHLY_PRICE_ID", "")
    STRIPE_PREMIUM_YEARLY_PRICE_ID = os.getenv("STRIPE_PREMIUM_YEARLY_PRICE_ID", "")
    STRIPE_CREDITS_100K_PRICE_ID = os.getenv("STRIPE_CREDITS_100K_PRICE_ID", "")
    PREMIUM_MONTHLY_CREDITS = int(os.getenv("MALTAI_PREMIUM_MONTHLY_CREDITS", "100000"))
    PREMIUM_YEARLY_CREDITS = int(os.getenv("MALTAI_PREMIUM_YEARLY_CREDITS", "1200000"))

    # Provider OpenAI reserve aux comptes Premium/Admin. La cle reste serveur.
    PREMIUM_OPENAI_NAME = os.getenv("PREMIUM_OPENAI_NAME", "Maltai Premium OpenAI")
    PREMIUM_OPENAI_BASE_URL = os.getenv("PREMIUM_OPENAI_BASE_URL", "https://api.openai.com/v1")
    PREMIUM_OPENAI_API_KEY = os.getenv("PREMIUM_OPENAI_API_KEY", "")
    PREMIUM_OPENAI_MODEL = os.getenv("PREMIUM_OPENAI_MODEL", "gpt-4o-mini")
    PREMIUM_OPENAI_EMBED_MODEL = os.getenv("PREMIUM_OPENAI_EMBED_MODEL", "text-embedding-3-small")
    MANAGED_OPENAI_MONTHLY_USER_TOKEN_LIMIT = int(os.getenv("MALTAI_MANAGED_OPENAI_MONTHLY_USER_TOKEN_LIMIT", "100000"))
    MANAGED_OPENAI_MONTHLY_GLOBAL_TOKEN_LIMIT = int(os.getenv("MALTAI_MANAGED_OPENAI_MONTHLY_GLOBAL_TOKEN_LIMIT", "1000000"))
    MANAGED_OPENAI_MAX_INPUT_TOKENS = int(os.getenv("MALTAI_MANAGED_OPENAI_MAX_INPUT_TOKENS", "12000"))
    MANAGED_OPENAI_MAX_OUTPUT_TOKENS = int(os.getenv("MALTAI_MANAGED_OPENAI_MAX_OUTPUT_TOKENS", "800"))

    # Envoi d'emails (outil email_send de l'agent). Non configure = outil desactive.
    SMTP_HOST = os.getenv("SMTP_HOST", "")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM = os.getenv("SMTP_FROM", "")  # defaut: SMTP_USER
    SMTP_TLS = _bool("SMTP_TLS", True)


settings = Settings()
