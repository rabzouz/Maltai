# MaltaiAI

Workspace IA auto-hébergé, open-source et hackable. Inspiré d'Odysseus — interface moderne, agents puissants, déployé sur **[maltai.fr](https://maltai.fr)**.

```
◐ Maltai v1.1
```

## Stack

- **Backend** : FastAPI (Python 3.11+), SQLite (sans ORM)
- **LLM** : client compatible OpenAI → Ollama, vLLM, llama.cpp, OpenRouter, OpenAI…
- **Frontend** : HTML/CSS/JS vanilla, zéro build, streaming SSE
- **Mobile / PWA** : installable sur l'écran d'accueil, drawer coulissant, safe-areas iOS
- **Auth** : PBKDF2 + cookie signé HMAC, admin auto-créé au premier boot
- **Agent** : function calling OpenAI, boucle multi-étapes (max 8)
- **Mémoire vectorielle** : embeddings dans SQLite, recherche cosine pur Python
- **MCP** : connecte des serveurs MCP distants (Streamable HTTP, JSON-RPC 2.0)
- **Connecteurs** : bot Telegram natif + API externe à clé
- **Browser automation** : navigateur texte + Playwright/Chromium headless
- **Billing** : plans Basic/Premium/Admin, crédits, suivi tokens et coûts

## Fonctionnalités

### Interface
- Sidebar style Odysseus : Chat, Discussions, Outils, Notes, Tâches, Deep Research, Librairie, Thème
- Chaque section de sidebar s'ouvre dans une fenêtre dédiée, utilisable sur mobile
- Composer flottant centré avec toggle Agent, nom du modèle, textarea auto-resize
- Thème sombre avec accent teal (`#00d4c4`)
- Markdown rendu avec code coloré (highlight.js), tableaux, citations
- Footer d'usage sous les réponses : provider, modèle, mode, tokens, crédits ou statut admin
- Console admin : terminal, explorateur de fichiers et gestion de process avec logs + kill

### Outils de l'agent

| Outil | Description | Accès |
|---|---|---|
| `calculator` | Arithmétique (eval AST sécurisé) | tous |
| `get_datetime` | Date/heure courante du serveur | tous |
| `list/read/write_file` | Fichiers du workspace (sandbox par user) | tous |
| `web_search` | Recherche DuckDuckGo (sans clé API) | tous |
| `web_fetch` | Lecture d'une page web + SSRF protection | tous |
| `browser_navigate/snapshot/links` | Navigateur texte : ouvrir une page, extraire texte/liens/formulaires | tous |
| `browser_form_list/submit` | Liste et soumission HTTP de formulaires simples | tous |
| `browser_open/click/type/screenshot` | Automatisation Chromium via Playwright + captures PNG | tous |
| `page_summary` | Résumé d'une page web | tous |
| `http_request` | Requête HTTP vers une API publique | tous |
| `memory_search` | Recherche dans la mémoire vectorielle | tous |
| `memory_save` | Ajoute un souvenir explicite en mémoire vectorielle | tous |
| `session_search` | Recherche dans les anciennes conversations | tous |
| `patch_file` | Remplace un bloc de texte dans un fichier workspace | tous |
| `wikipedia` | Résumé d'article Wikipédia (fr) | tous |
| `weather` | Météo + prévisions 3 jours (Open-Meteo, sans clé) | tous |
| `rss_fetch` | Derniers articles d'un flux RSS/Atom | tous |
| `youtube_transcript` | Transcription d'une vidéo YouTube | tous |
| `image_generate` | Génère une image (endpoint compatible OpenAI) | tous |
| `deep_research` | Recherche web multi-étapes → rapport markdown avec sources | tous |
| `code_execute` | Code Python sandbox isolé, timeout 10s, imports système bloqués | tous |
| `python_exec` | Code Python isolé (`-I`), timeout 30s | admin |
| `shell` | Commande shell, timeout 30s | admin |
| `git_status/branch/log/diff/show` | Inspection Git read-only de l'installation Maltai | admin |
| `note_add/list/delete` | Notes persistantes | tous |
| `todo_add/list/done` | Tâches avec statut | tous |
| `mcp_*` | Outils des serveurs MCP connectés | tous |

### Gestion Ollama intégrée
Depuis **Réglages → Modèles Ollama** :
- liste des modèles installés ;
- téléchargement via l'API Ollama `/api/pull` avec barre de progression ;
- suppression d'un modèle ;
- fallback Docker vers `host.docker.internal` et `10.0.1.1`.

### Console admin
La console intégrée contient :
- **Terminal** : commandes rapides `version`, `health`, `process`, `models`, commandes libres ;
- **Fichiers** : navigation du workspace utilisateur, édition texte, téléchargement images/gros fichiers ;
- **Process** : lancer une commande longue, suivre les logs, arrêter avec `Kill`.

Les outils Git admin (`git_status`, `git_branch`, `git_log`, `git_diff`, `git_show`) sont en lecture seule : ils servent à vérifier la version installée, la branche, les derniers commits et les changements locaux sans exposer de commit/push aux utilisateurs.

En Docker/Coolify, le dossier `.git` n'est généralement pas copié dans l'image. Dans ce cas Maltai affiche la version de l'app et les variables optionnelles `MALTAI_GIT_BRANCH` / `MALTAI_GIT_COMMIT` si elles sont définies.

### Browser automation
Deux niveaux sont disponibles :
- **Léger HTTP** : `browser_snapshot`, `browser_links`, `browser_form_list`, `browser_submit` ;
- **Réel Chromium** : `browser_open`, `browser_click`, `browser_type`, `browser_screenshot`.

Les screenshots sont sauvegardés dans le workspace, par exemple :
`browser_screenshots/aqua-reservation.png`, avec lien de téléchargement dans l'UI.

### Deep Research
Enchaîne : plan de requêtes → recherches web → lecture des meilleures pages → rapport markdown structuré avec sources. Accessible via le panneau **Deep Research** dans la sidebar ou en mode 🛠 Agent.

### Panneau Deep Research (UI)
Clique sur **Deep Research** dans la sidebar → entre un sujet → rapport généré directement dans l'interface sans passer par le chat.

### Bot Telegram
Parle à ton Maltai depuis Telegram :
1. Crée un bot via **@BotFather** → récupère le jeton
2. **⚙ Réglages → Connecteurs → Telegram** : colle le jeton + URL publique + active
3. Envoie un message → le bot répond ton chat ID → ajoute-le à la liste blanche

Sécurité : webhook avec secret dans l'URL + header `X-Telegram-Bot-Api-Secret-Token` ; jamais d'outil `shell` via Telegram.

### Mémoire vectorielle
Mémorise les conversations et rappelle le contexte pertinent d'une session à l'autre. Active en configurant un modèle d'embeddings sur le provider.

Le panneau **Brain** permet :
- lister les souvenirs ;
- rechercher dans la mémoire ;
- filtrer `user` / `assistant` ;
- épingler / désépingler un souvenir ;
- supprimer un souvenir précis ;
- supprimer les résultats filtrés sans toucher aux souvenirs épinglés.

### Plans, crédits et usage
- `Basic` : chat principal.
- `Premium` : outils agent.
- `Admin` : accès complet, usage non facturé.

Chaque réponse affiche un footer d'usage :
`provider · modèle · Chat/Agent · tokens in/out · crédits · solde`.

## Démarrage rapide

```bash
git clone https://github.com/rabzouz/Maltai.git
cd Maltai
python3 -m venv venv
source venv/bin/activate          # Windows : venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Ouvre http://localhost:7000 — connecte-toi avec `admin` et le mot de passe temporaire affiché dans le terminal.

### Avec Ollama (local)

```bash
ollama serve
ollama pull llama3.1:8b
```

### Avec OpenAI / OpenRouter

Dans **Réglages → Providers**, ajoute :
- **OpenAI** : `https://api.openai.com/v1` + ta clé
- **OpenRouter** : `https://openrouter.ai/api/v1` + ta clé

## Déploiement (Coolify / Docker)

```bash
docker compose up --build   # http://localhost:7000
```

Sur Coolify : HTTPS automatique, stockage persistant sur `/app/data`, secrets en variables d'environnement. Chaque push sur `main` redéploie automatiquement.

**Live** : [https://maltai.fr](https://maltai.fr)

## Architecture

```
app.py              # point d'entrée FastAPI
core/
  config.py         # réglages (.env)
  database.py       # schéma + helpers SQLite
  auth.py           # PBKDF2 + cookies signés + seed admin
src/
  llm.py            # client LLM compatible OpenAI (streaming + tool calls)
  tools.py          # registre d'outils : fichiers, web, browser, code, mémoire, etc.
  agent.py          # boucle agentique multi-étapes
  ollama.py         # client API Ollama
  memory.py         # embeddings + rappel vectoriel
  telegram.py       # connecteur Telegram (webhook sécurisé)
  connector.py      # moteur partagé (Telegram, API externe)
routes/
  auth.py           # login / logout / me / change-password
  providers.py      # CRUD providers + liste des modèles
  ollama.py         # gestion modèles Ollama
  memory.py         # Brain manager
  terminal.py       # console admin, fichiers, process
  sessions.py       # sessions + messages
  chat.py           # chat simple ou agent, en streaming (SSE)
  notes.py          # notes & tâches (CRUD)
  tool_run.py       # exécution directe d'outils depuis l'UI
  tools.py          # catalogue des outils disponibles
  uploads.py        # uploads + téléchargement workspace
  telegram.py       # webhook Telegram + config
static/
  index.html, style.css, js/app.js
data/               # app.db (gitignore)
```

## Sécurité

- `AUTH_ENABLED=true` par défaut
- Workspace isolé par utilisateur (`data/workspace/<user_id>/`)
- SSRF protection sur `web_fetch`, `page_summary`, `http_request` et les outils browser pour les non-admins
- Outils sensibles (`shell`, `python_exec`, `git_*`) réservés aux admins
- Console admin réservée aux admins
- Suppression mémoire filtrée protège les souvenirs épinglés
- `SECURE_COOKIES=true` en production (HTTPS)
- Sessions signées HMAC, pas de JWT tiers

## Variables d'environnement

| Variable | Défaut | Description |
|---|---|---|
| `SESSION_SECRET` | auto-généré | Secret HMAC (définir en prod) |
| `SECURE_COOKIES` | `true` | Cookies sécurisés (HTTPS) |
| `AUTH_ENABLED` | `true` | Authentification obligatoire |
| `MALTAI_ADMIN_PASSWORD` | affiché console | Mot de passe admin initial |
| `APP_BIND` | `0.0.0.0` | Adresse d'écoute |
| `APP_PORT` | `7000` | Port |
| `MEMORY_ENABLED` | `true` | Mémoire vectorielle |
| `MEMORY_TOP_K` | `4` | Souvenirs rappelés par message |

## Licence

MIT — voir `LICENSE`.
