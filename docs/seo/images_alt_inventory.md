# Inventaire des attributs `alt` — homepage

| Fichier | Emplacement | Contexte | alt appliqué |
|---|---|---|---|
| `/static/logo-emblem.png` | header (lien marque) | logo cliquable | `Logo MaltaiAI` |
| `/static/logo-full.png` | hero (arrière-plan) | filigrane décoratif (parent `aria-hidden`) | `MaltaiAI — workspace IA open source et auto-hébergé` |
| `/static/og-image.png` | meta Open Graph | image de partage social | n/a (méta, pas de balise `<img>`) |

## Règles retenues
- Toute balise `<img>` visible porte un `alt` non vide.
- Le filigrane du hero reste dans un conteneur `aria-hidden="true"` : l'`alt` sert
  au crawler SEO sans pollution pour les lecteurs d'écran.
- Largeur/hauteur (`width`/`height`) renseignées pour limiter le CLS.

## Vérification
- Inspecter le HTML : `grep -n "<img" static/site.html` → aucun `alt=""`.
- Test accessibilité (axe / Lighthouse) : 0 image sans texte alternatif.
- Re-crawl SEO : section « images sans alt » à 0.
