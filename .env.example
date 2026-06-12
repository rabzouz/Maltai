# Maltai - exemple de configuration. Copier en .env (optionnel).
# Les valeurs par defaut fonctionnent en local sans rien configurer.

APP_BIND=127.0.0.1
APP_PORT=7000

# Provider par defaut cree au premier demarrage (compatible OpenAI).
DEFAULT_PROVIDER_NAME=Ollama local
DEFAULT_BASE_URL=http://localhost:11434/v1
DEFAULT_API_KEY=ollama
DEFAULT_MODEL=llama3.1:8b

# Base de donnees
# DATABASE_URL=sqlite:///./data/app.db

# Auth (pas encore branchee dans le MVP - cf. ROADMAP)
AUTH_ENABLED=true
LOCALHOST_BYPASS=false
SECURE_COOKIES=false

# --- Mémoire vectorielle ---
MEMORY_ENABLED=true
MEMORY_TOP_K=4
MEMORY_MIN_SCORE=0.3
# Modèle d'embeddings du provider seedé (Ollama: nomic-embed-text, mxbai-embed-large…)
DEFAULT_EMBED_MODEL=nomic-embed-text

# --- Generation d'images (optionnel) ---
# Endpoint compatible OpenAI /v1/images/generations :
# OpenAI, LocalAI, Stable Diffusion webui (--api), ComfyUI via wrapper OpenAI...
# IMAGE_API_BASE=https://api.openai.com
# IMAGE_API_KEY=sk-...
# IMAGE_MODEL=gpt-image-1
