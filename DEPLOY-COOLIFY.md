# ROADMAP Maltai

v0.2 actuel : chat multi-providers, sessions, streaming, **auth (cookie
signé, admin seedé)**, **agent + outils (calc, fichiers sandbox, web
search/fetch, shell admin)**, **mobile + PWA installable**, **déploiement
Docker/Coolify**, **mémoire vectorielle**, **client MCP (Streamable HTTP)**, **rendu Markdown**, **bot Telegram + API
externe a cle**, **upload fichiers (PDF/texte/images vision)**, **workspace
telechargeable**, **outils additionnels (datetime, http, memoire, python)**.

## Prochaines briques (par ordre de valeur / effort)

### 1. ~~Auth & comptes~~ ✅ fait (v0.2)
- Reste : multi-utilisateurs (signup, gestion par l'admin), sessions par user

### 2. ~~Mémoire~~ ✅ fait (v0.5)
- ✅ Embeddings via provider + stockage SQLite + recall auto dans le prompt
- Reste : migration `sqlite-vec` pour le passage à l'échelle, extraction de
  « faits » saillants (vs message brut), import/export, mémoire éditable

### 3. ~~Agents + outils + MCP~~ ✅ fait (v0.2 / v0.6)
- ✅ Serveurs MCP distants en Streamable HTTP (decouverte + execution)
- Reste : OAuth MCP, serveurs stdio locaux (hors conteneur), confirmation
  utilisateur avant actions sensibles, skills persistantes

### 4. ~~Recherche web~~ ✅ fait (v0.2 / v1.1)
- ✅ DuckDuckGo + deep research multi-etapes -> rapport avec sources
- Reste : integration SearXNG (recherche auto-hebergee)

### 5. ~~Documents & uploads~~ ✅ fait (v0.8)
- ✅ Upload PDF/texte/images vision, extraction, limite de taille
- Reste : éditeur multi-onglets, OCR des PDF scannés, historique multimodal
  (les images ne sont envoyées qu'au tour de leur upload)

### 5bis. ~~Interface agent~~ ✅ fait (v0.9)
- ✅ Panneau de selection des outils (natifs + MCP), cartes d'appels
  repliables, bouton stop, copie des reponses

### 6. Confort
- ~~Markdown + coloration syntaxique dans les bulles~~ ✅ fait (v0.7)
- ~~Copier, edition et regeneration de messages~~ ✅ (v0.9 / v1.0)
- Thèmes, presets, raccourcis
- ~~PWA / mobile~~ ✅ fait (v0.3)

### 7. Déploiement
- ~~Dockerfile + docker-compose~~ ✅ fait (v0.4)
- Overlays GPU (nvidia/amd) pour servir des modèles localement
- ~~Reverse proxy HTTPS~~ ✅ via Coolify/Traefik (v0.4)
