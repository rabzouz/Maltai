# Maltai — Mise à jour v1.3

## Fichiers modifiés

| Fichier | Changements |
|---|---|
| `core/database.py` | Table `skills`, FTS5 `messages_fts`, auto-indexation dans `add_message`, fonctions `skill_save/list/get`, `fts_search` |
| `src/tools.py` | 6 nouveaux outils : `memory_save`, `session_search`, `patch_file`, `skill_save`, `skill_list`, `skill_run` |

## Nouveaux outils (30 total)

### memory_save
L'agent mémorise durablement un fait dans la mémoire vectorielle persistante.
Ex : "Mémorise que je préfère les réponses courtes."

### session_search
Recherche plein texte FTS5 dans toutes les conversations passées.
Ex : "Recherche dans mes discussions les échanges sur Docker."

### patch_file
Remplace un bloc ciblé dans un fichier du dossier workspace/ sans réécrire tout le fichier.

### skill_save / skill_list / skill_run
Sauvegarde, liste et rappelle des procédures réutilisables en base de données.

## Déploiement (3 étapes)

1. Copier dans votre repo GitHub :
   - core/database.py
   - src/tools.py

2. Pousser :
   git add core/database.py src/tools.py
   git commit -m "feat: pack v1.3"
   git push

3. Redéployer dans Coolify → Redeploy.

La migration DB est automatique. Aucune perte de données.

Note : patch_file utilise un dossier workspace/ créé automatiquement à côté de data/.
Pour le persister entre redéploiements, ajoutez un volume mount /app/workspace dans Coolify.
