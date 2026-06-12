# Déployer Maltai sur Coolify (Hostinger)

Coolify détecte le `Dockerfile` à la racine, build l'image, et gère le HTTPS
automatiquement (Traefik + Let's Encrypt). C'est ce HTTPS qui rend la **PWA
installable** depuis ton téléphone.

## 1. Mettre Maltai sur un dépôt Git

Coolify déploie depuis Git (GitHub / GitLab…). Pousse le dossier `maltai/`
sur un repo (privé de préférence) :

```bash
cd maltai
git init && git add . && git commit -m "Maltai v0.4"
git remote add origin git@github.com:ton-compte/maltai.git
git push -u origin main
```

## 2. Créer l'application dans Coolify

1. **+ New Resource → Application**
2. Choisis ta source Git, sélectionne le repo `maltai` et la branche `main`
3. **Build Pack** : laisse Coolify détecter — il trouvera le `Dockerfile`
4. **Port** : `7000` (normalement auto-détecté via `EXPOSE`)
5. **Domaine** : mets ton sous-domaine (ex. `maltai.tondomaine.fr`). Coolify
   provisionne le certificat HTTPS tout seul.

## 3. Stockage persistant (IMPORTANT)

Sans ça, ta base SQLite et tes comptes sont effacés à chaque redéploiement.

- Onglet **Storages → + Add**
- **Name** : `maltai-data`
- **Destination Path** : `/app/data`  ← le dossier (pas un fichier)

Tout y est stocké : `app.db`, `secret.key`, le workspace de l'agent.

## 4. Variables d'environnement

Onglet **Environment Variables** (chiffrées au repos, hors Git) :

| Clé                     | Valeur                                   |
| ----------------------- | ---------------------------------------- |
| `SECURE_COOKIES`        | `true`                                   |
| `SESSION_SECRET`        | 32+ caractères aléatoires (voir ci-dessous) |
| `MALTAI_ADMIN_USER`     | `admin` (ou ton identifiant)             |
| `MALTAI_ADMIN_PASSWORD` | un mot de passe fort                     |

Génère un secret :

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

`SESSION_SECRET` est facultatif (sinon un secret est généré et stocké dans le
volume persistant), mais le définir évite de déconnecter tout le monde si le
volume est recréé.

## 5. Déployer

Clique **Deploy**. Au premier boot, le compte admin est créé avec le mot de
passe que tu as mis dans `MALTAI_ADMIN_PASSWORD`. Sinon, un mot de passe
temporaire s'affiche dans les **logs de déploiement** Coolify.

Ouvre ton domaine, connecte-toi, et installe la PWA depuis le navigateur du
téléphone (« Ajouter à l'écran d'accueil »).

## Sécurité — à exposer sur Internet

Maltai exposé publiquement est puissant (l'agent a accès web, fichiers, et
shell pour les admins). Donc :

- **Mot de passe admin fort** et `AUTH_ENABLED=true` (défaut).
- L'outil `shell` reste réservé aux admins — n'ajoute pas d'autres comptes
  admin sans raison.
- Pense à un backup régulier du volume `maltai-data` (Coolify sait sauvegarder
  les volumes).
- Pour un provider LLM : soit tu pointes vers une API (OpenAI/OpenRouter avec
  clé en variable d'env), soit vers ton Ollama. Si Ollama tourne ailleurs,
  expose-le **uniquement** sur ton réseau privé (Tailscale), pas en clair.

## Mettre à jour

Push sur la branche → **Deploy** (ou active le déploiement auto sur push dans
Coolify). Le volume `/app/data` est conservé entre les versions.
