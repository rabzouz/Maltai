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

# Dependances d'abord (cache de build).
COPY requirements.txt .
RUN pip install -r requirements.txt

# Code applicatif.
COPY . .

# Dossier de donnees (sera monte en volume persistant par Coolify).
RUN mkdir -p data/workspace

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

CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7000"]
