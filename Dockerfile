FROM python:3.12-slim

# Reglages Python sains pour un conteneur.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# git : utile a l'outil shell de l'agent et a certains workflows.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl \
    && rm -rf /var/lib/apt/lists/*

# Navigateurs Playwright dans un chemin partage (lisible par l'utilisateur app).
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Dependances d'abord (cache de build).
COPY requirements.txt .
RUN pip install -r requirements.txt
RUN python -m playwright install --with-deps chromium

# Utilisateur applicatif non-root.
RUN useradd --create-home --uid 1000 maltai \
    && chmod -R a+rX /ms-playwright

# Code applicatif.
COPY . .

# Dossier de donnees (sera monte en volume persistant par Coolify).
RUN mkdir -p data/workspace && chown -R maltai:maltai /app

# Commit git injecte par Coolify au build (affiche par /api/health).
ARG SOURCE_COMMIT=""
ENV SOURCE_COMMIT=$SOURCE_COMMIT

# Derriere le proxy Coolify : on ecoute sur toutes les interfaces, en HTTPS
# termine par Traefik, donc cookies securises.
ENV APP_BIND=0.0.0.0 \
    APP_PORT=7000 \
    SECURE_COOKIES=true \
    AUTH_ENABLED=true

EXPOSE 7000

# Healthcheck pour Coolify (endpoint public, non authentifie).
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:7000/api/health || exit 1

# Demarrage : corrige les droits du volume data (monte root par Docker) puis
# bascule sur l'utilisateur non-root. Si setpriv est absent, repli en root
# (comportement identique a avant, aucun risque de panne).
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7000"]
