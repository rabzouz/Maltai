# Redirection 301 canonique — maltai.fr

**Objectif :** éviter le duplicate content entre `www.maltai.fr` et `maltai.fr`.
**URL canonique officielle :** `https://maltai.fr/`

## Règles à appliquer
| Source | Destination | Code |
|---|---|---|
| `http://maltai.fr/*` | `https://maltai.fr/*` | 301 |
| `http://www.maltai.fr/*` | `https://maltai.fr/*` | 301 |
| `https://www.maltai.fr/*` | `https://maltai.fr/*` | 301 |

## Mise en œuvre

### 1. Niveau application (déjà en place)
Un middleware dans `app.py` redirige tout hôte `www.*` vers la version sans `www`
en 301, chemin et query préservés :

```python
@app.middleware("http")
async def canonical_host_redirect(request, call_next):
    host = request.headers.get("host", "")
    if host.startswith("www."):
        target = f"https://{host[4:]}{request.url.path}"
        if request.url.query:
            target += "?" + request.url.query
        return RedirectResponse(target, status_code=301)
    return await call_next(request)
```

> Le passage `http → https` est géré par le proxy/Coolify (TLS terminé en amont).

### 2. Niveau proxy (recommandé en complément — Coolify / Traefik)
Idéalement, traiter la redirection avant l'app. Avec Traefik (Coolify) :

```yaml
# Labels du service
- "traefik.http.routers.maltai-www.rule=Host(`www.maltai.fr`)"
- "traefik.http.routers.maltai-www.middlewares=maltai-redir-nonwww"
- "traefik.http.middlewares.maltai-redir-nonwww.redirectregex.regex=^https?://www\\.maltai\\.fr/(.*)"
- "traefik.http.middlewares.maltai-redir-nonwww.redirectregex.replacement=https://maltai.fr/$${1}"
- "traefik.http.middlewares.maltai-redir-nonwww.redirectregex.permanent=true"
```

Si `www` n'est pas censé exister, ne pas créer d'enregistrement DNS/cert `www`
suffit aussi à supprimer le risque.

## Cohérence à vérifier
- Balise `<link rel="canonical" href="https://maltai.fr/">` ✔ (déjà présente)
- `sitemap.xml` ne contient que des URL canoniques `https://maltai.fr/` ✔
- Liens internes en chemins relatifs (`/app`, `/login`) ✔
- Google Search Console : déclarer la propriété `https://maltai.fr/`

## Vérification
```bash
curl -sI https://www.maltai.fr/        | grep -i "^HTTP\|^location"   # attendu : 301 -> https://maltai.fr/
curl -sI http://maltai.fr/             | grep -i "^HTTP\|^location"   # attendu : 301/308 -> https
curl -sI https://maltai.fr/            | grep -i "^HTTP"              # attendu : 200
```
**Sortie attendue :** chaque variante non canonique répond en 301 vers `https://maltai.fr/...`.
