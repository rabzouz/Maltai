# Maltai

Workspace IA auto-hébergé, minimal et hackable. Inspiré d'Odysseus, mais
réduit à un socle clair que tu peux étendre : **chat multi-providers
(compatible OpenAI), agents avec outils, auth, sessions persistées,
frontend vanilla**.

```
◐ Maltai v1.1
```

## Stack

- **Backend** : FastAPI (Python 3.11+), SQLite (sqlite3 standard, sans ORM)
- **LLM** : client unique compatible OpenAI → Ollama, vLLM, llama.cpp,
  OpenRouter, OpenAI… (il suffit de changer `base_url` + `api_key` + `model`)
- **Frontend** : HTML/CSS/JS vanilla, zéro build, streaming SSE
- **Mobile / PWA** : drawer coulissant, safe-areas iOS, cibles tactiles,
  installable sur l'écran d'accueil (manifest + service worker)
- **Auth** : 100% stdlib — PBKDF2 + cookie signé HMAC, admin auto-créé au
  premier boot (mot de passe temporaire affiché en console)
- **Agent** : function calling OpenAI, boucle multi-étapes (max 8), outils :
  `calculator`, `read/write/list_files` (sandbox `data/workspace/`),
  `web_search` (DuckDuckGo), `web_fetch`, `shell` (admin seulement, 30s)
- **Mémoire vectorielle** : chaque message est embarqué (endpoint
  `/v1/embeddings` du provider) et stocké dans SQLite ; au message suivant, les
  souvenirs pertinents des conversations passées sont rappelés et injectés
  automatiquement. Recherche cosine en pur Python, zéro dépendance native.
- **MCP (Model Context Protocol)** : connecte des serveurs MCP distants
  (transport Streamable HTTP, JSON-RPC 2.0) — leurs outils sont découverts
  automatiquement et fusionnés avec les outils natifs de l'agent.
- **Markdown** : réponses rendues avec code coloré (highlight.js) + bouton
  copier, tableaux, citations — libs vendorisées localement (PWA offline OK).
- **Connecteurs** : bot **Telegram** natif (webhook sécurisé, liste blanche de
  chats) et **API externe** à clé pour brancher OpenClaw, scripts, ou tout
  autre système.
- **Fichiers joints** : PDF (texte extrait), fichiers texte/code, et images
  envoyées en base64 aux modèles vision (llava, llama3.2-vision…).
- **Workspace** : les fichiers créés par l'agent sont listés et
  téléchargeables depuis les Réglages.

## Démarrage rapide

```bash
cd maltai
python3 -m venv venv
source venv/bin/activate          # Windows : venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Puis ouvre http://localhost:7000 — connecte-toi avec `admin` et le mot de
passe temporaire affiché dans le terminal, puis change-le dans
**⚙ Réglages > Compte**.

Pour utiliser les outils, coche **🛠 Agent** dans la zone de saisie
(le modèle doit supporter le function calling : llama3.1, qwen2.5, mistral…).

Au premier lancement, un provider **Ollama local** est créé automatiquement.
Ajoute/édite tes providers via le bouton **⚙ Réglages**.

### Avec Ollama (le plus simple en local)

```bash
ollama serve
ollama pull llama3.1:8b
```

Maltai pointe par défaut sur `http://localhost:11434/v1`.

### Avec un autre backend

Dans **Réglages**, ajoute un provider :
- **OpenAI** : `https://api.openai.com/v1` + ta clé
- **OpenRouter** : `https://openrouter.ai/api/v1` + ta clé
- **vLLM / llama.cpp** : l'URL de ton serveur local

## Architecture

```
app.py              # point d'entree FastAPI
core/
  config.py         # reglages (.env)
  database.py       # schema + helpers SQLite
  auth.py           # PBKDF2 + cookies signes + seed admin
src/
  llm.py            # client LLM compatible OpenAI (streaming + tool calls)
  tools.py          # registre d'outils (calc, fichiers, web, shell)
  agent.py          # boucle agentique multi-etapes
routes/
  auth.py           # login / logout / me / change-password
  providers.py      # CRUD providers + liste des modeles
  sessions.py       # sessions + messages
  chat.py           # chat simple ou agent, en streaming (SSE)
static/
  index.html, style.css, js/app.js
data/               # app.db (gitignore)
```

## Mobile & PWA

L'interface s'adapte au téléphone : la sidebar devient un tiroir coulissant
(ouvert via ☰, fermé en tapant l'overlay ou en choisissant une discussion),
les zones de saisie respectent les encoches iOS, et la saisie est en 16px
pour éviter le zoom automatique de Safari.

**Installer comme app** : ouvre Maltai dans le navigateur du téléphone, puis
« Ajouter à l'écran d'accueil ». L'icône ◐ apparaît et l'app se lance en
plein écran. Le service worker met en cache uniquement le shell statique —
jamais les appels API ni l'authentification.

> Note : l'installation PWA nécessite HTTPS (sauf sur `localhost`). Sers
> Maltai derrière un reverse proxy HTTPS ou via Tailscale pour l'installer
> depuis un autre appareil.

## Sécurité

- Garde `AUTH_ENABLED=true` dès que tu exposes hors localhost.
- L'outil `shell` n'est accessible qu'aux admins (timeout 30s, cwd =
  `data/workspace/`). Les outils fichiers ne peuvent pas sortir du workspace.
- Pas de HTTPS intégré : mets un reverse proxy (Caddy/nginx) devant.

## Fichiers joints & workspace

Le bouton 📎 du composer accepte PDF, texte/code (txt, md, csv, json, py,
js…), et images (png, jpg, webp, gif), 10 Mo max par fichier
(`MALTAI_CHAT_UPLOAD_MAX_BYTES`).

- **PDF / texte** : le contenu est extrait et injecté dans le contexte du
  modèle — n'importe quel modèle de chat fonctionne.
- **Images** : envoyées en base64 (format OpenAI `image_url`) — nécessite un
  modèle **vision** (ex. `ollama pull llama3.2-vision` ou `llava`).
- Les fichiers créés par l'agent (`write_file`, `shell`, `python_exec`) sont
  téléchargeables dans **Réglages → Workspace de l'agent**.

## Interface agent

- **Panneau d'outils** (bouton ▾ à côté du toggle 🛠 Agent) : active/désactive
  chaque outil — natifs et MCP — pour tes conversations ; préférences
  mémorisées dans le navigateur. Un outil décoché n'est jamais proposé au
  modèle ni exécuté.
- **Cartes d'appels d'outils** : chaque appel s'affiche en carte repliable
  (statut en cours/✓/✗, aperçu des arguments, sections Arguments/Résultat).
- **Bouton stop** ■ pour interrompre une génération en cours.
- **Copier** sous chaque réponse de l'assistant.
- **↻ Régénérer** la dernière réponse (l'échange est rejoué sur le même
  historique) et **✎ Modifier** n'importe lequel de tes messages — la suite
  de la conversation est tronquée et ton texte revient dans le composer.

## Outils de l'agent

| Outil | Description | Accès |
| --- | --- | --- |
| `calculator` | Arithmétique (eval AST sécurisé) | tous |
| `get_datetime` | Date/heure courante du serveur | tous |
| `list/read/write_file` | Fichiers du workspace (sandbox) | tous |
| `web_search` | Recherche DuckDuckGo | tous |
| `web_fetch` | Lecture d'une page web | tous |
| `http_request` | Requête HTTP vers une API publique (anti-SSRF : hôtes privés bloqués) | tous |
| `memory_search` | Recherche dans la mémoire vectorielle | tous |
| `wikipedia` | Résumé d'article Wikipédia (fr) | tous |
| `weather` | Météo + prévisions 3 jours (Open-Meteo, sans clé) | tous |
| `rss_fetch` | Derniers articles d'un flux RSS/Atom | tous |
| `youtube_transcript` | Transcription d'une vidéo YouTube | tous |
| `generate_image` | Génère une image et la sauve dans le workspace (endpoint configurable) | tous |
| `deep_research` | Recherche web multi-étapes → rapport markdown avec sources | tous |
| `python_exec` | Code Python isolé (`-I`), timeout 30 s | admin |
| `shell` | Commande shell, timeout 30 s | admin |
| `mcp_*` | Outils des serveurs MCP connectés | tous |

### Deep research

L'outil `deep_research` enchaîne : plan de requêtes (généré par le modèle) →
3 recherches web → lecture des meilleures pages (domaines distincts) →
rapport markdown structuré avec sources. À invoquer en mode 🛠 Agent :
*« fais une recherche approfondie sur X »*. Compte ~1-2 min et davantage de
tokens qu'une réponse classique.

### Génération d'images

`generate_image` appelle un endpoint compatible OpenAI
(`/v1/images/generations`) configuré via `IMAGE_API_BASE` / `IMAGE_API_KEY` /
`IMAGE_MODEL` : OpenAI, LocalAI, Stable Diffusion webui (`--api`), ou ton
ComfyUI derrière un wrapper OpenAI. L'image est sauvée dans le workspace et
téléchargeable depuis les Réglages.

## Connecteurs : Telegram & API externe

### Bot Telegram

Parle à ton Maltai depuis Telegram, comme avec OpenClaw — mais intégré
nativement, sans gateway supplémentaire.

1. Crée un bot via **@BotFather** → récupère le jeton.
2. **⚙ Réglages → Connecteurs → Telegram** : colle le jeton, mets l'URL
   publique de ton Maltai (ton domaine Coolify), active.
3. Envoie un message au bot : il répond ton **chat ID** — ajoute-le à la
   liste blanche dans les Réglages. À partir de là, le bot te répond, avec la
   mémoire vectorielle et (optionnel) le mode agent.

Sécurité : webhook avec secret dans l'URL **et** vérification du header
`X-Telegram-Bot-Api-Secret-Token` ; chats non listés refusés ; jamais d'outil
`shell` via Telegram.

### API externe (OpenClaw, scripts, automations)

Génère une clé dans **Réglages → API externe**, puis :

```bash
curl -X POST https://ton-maltai.fr/api/external/chat \
  -H "X-Api-Key: mlt_..." \
  -H "Content-Type: application/json" \
  -d '{"session_key": "openclaw", "message": "salut", "agent": false}'
```

`session_key` est un identifiant libre : chaque clé de session a sa propre
conversation continue (visible aussi dans l'UI web). Pour OpenClaw : une
skill HTTP qui POST sur cet endpoint suffit pour que ton assistant OpenClaw
interroge Maltai (sa mémoire, ses outils MCP…).

## Serveurs MCP

Maltai parle le **Model Context Protocol** côté client : ajoute n'importe quel
serveur MCP distant (URL + jeton optionnel) dans **⚙ Réglages → Serveurs
MCP**, teste-le avec 🔌, et ses outils deviennent disponibles en mode
🛠 Agent — préfixés `mcp_<serveur>_<outil>` pour éviter les collisions.

- Transport : **Streamable HTTP** (POST JSON-RPC, réponses JSON ou SSE),
  adapté au déploiement conteneurisé — aucun processus local requis.
- Les serveurs sont interrogés à chaque requête agent ; un serveur injoignable
  est ignoré silencieusement (l'agent continue avec le reste).
- Active/désactive un serveur sans le supprimer (⏸ / ▶).
- Auth : jeton Bearer optionnel, stocké en base, jamais renvoyé par l'API.

## Mémoire

Maltai mémorise les conversations et rappelle le contexte pertinent d'une
session à l'autre — comme un assistant qui se souvient de ce que vous lui avez
dit la semaine dernière.

**Activation** : il suffit de renseigner un **modèle d'embeddings** sur le
provider (champ « Modèle d'embeddings / mémoire » dans Réglages). Sans lui, la
mémoire reste inactive et le chat fonctionne normalement.

Avec Ollama :
```bash
ollama pull nomic-embed-text     # ou mxbai-embed-large
```

- Réglages affiche le nombre de souvenirs et permet de tout effacer.
- Variables : `MEMORY_ENABLED`, `MEMORY_TOP_K` (nb rappelés/message),
  `MEMORY_MIN_SCORE` (seuil de pertinence 0–1).
- La recherche est en pur Python (brute-force cosine), parfaite pour une
  instance perso. Au-delà de ~50k souvenirs, migrer vers `sqlite-vec`.

## Déploiement

Maltai a un `Dockerfile` et un `docker-compose.yml`.

**En local (Docker)** :
```bash
docker compose up --build   # http://localhost:7000
```

**Sur un serveur (Coolify, Dokploy, etc.)** : voir `DEPLOY-COOLIFY.md` —
HTTPS automatique (donc PWA installable), stockage persistant sur `/app/data`,
secrets en variables d'environnement.

## Roadmap

Voir `ROADMAP.md`. Prochaines briques : mémoire vectorielle, MCP, documents,
upload fichiers (vision/PDF), Docker.

## Publier sur GitHub

```bash
cd maltai
git init && git add . && git commit -m "Maltai v0.6"
git branch -M main
git remote add origin git@github.com:<ton-compte>/maltai.git
git push -u origin main
```

Le `.gitignore` exclut déjà `data/` (base, secrets, workspace) et `.env` —
aucun secret ne part dans le repo. Sur Coolify, pointe l'application sur ce
repo : chaque push redéploie (voir `DEPLOY-COOLIFY.md`).

## Licence

MIT — voir `LICENSE`. Maltai est écrit de zéro (architecture inspirée
d'Odysseus, sans réutilisation de code).
