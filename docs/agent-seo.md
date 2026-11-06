# Agent SEO / SEO local / GEO

Panneau **Agent SEO** dans l'app (menu de gauche) + 6 outils utilisables par l'agent.
Accès : plans **Premium** et **Admin** (outils agent), facturés en crédits.

| Outil | Rôle | Crédits |
|---|---|---|
| `seo_site_audit` | Audit technique multi-pages (1-30) : https, www/non-www, robots.txt, sitemap, soft 404, title/meta/H1/alt/canonical/noindex, contenu mince, doublons, pages en erreur. Score A-F, problèmes P0/P1/P2. | 120 |
| `local_seo_audit` | SEO local : schéma LocalBusiness, cohérence NAP, lien `tel:`, carte, horaires, avis, ville dans title/H1, brouillon JSON-LD + checklist Google Business Profile. | 60 |
| `geo_audit` | GEO : 18 bots IA dans robots.txt (recherche vs entraînement), `/llms.txt`, données structurées, contenu citable, entité & confiance. Score /100 détaillé. | 60 |
| `schema_generate` | JSON-LD prêt à coller : localbusiness, organization, website, faqpage, softwareapplication, article, product, breadcrumblist, service. | 10 |
| `llms_txt_generate` | Génère un `llms.txt` (llmstxt.org) depuis un crawl du site. | 90 |
| `seo_keywords` | Mots-clés 1-3 mots, densité, présence title/H1/meta/H2/URL, écart avec un concurrent (`competitor_url`). | 30 |

Rapports complets exportés dans le workspace utilisateur, dossier `seo/` (JSON + Markdown).
Toutes les requêtes passent par `_safe_fetch` (anti-SSRF, redirections revalidées) ; le crawl respecte `robots.txt`.

## Utilisation
1. Ouvrir **Agent SEO**, saisir l'URL (et pour le local : nom + ville).
2. Choisir une mission (audit complet, SEO local, GEO, llms.txt, JSON-LD, mots-clés, concurrent, réécriture, plan 30 jours).
3. Le mode Agent s'active et le prompt est prêt dans le chat.

Les scores sont heuristiques : le score GEO mesure la capacité d'un site à être lu et cité par les moteurs IA,
pas sa présence réelle dans leurs réponses.

## Fichiers
- `src/seo_tools.py` (module), branché dans `src/tools.py` ; coûts dans `core/plans.py`
- UI : `static/index.html`, `static/js/app.js`, `static/style.css`
- maltai.fr : section « Agent SEO » + FAQ dans `static/site.html`, `static/llms.txt` servi sur `/llms.txt`
- `app.py` : vraie 404 HTML pour les URL inexistantes (plus de « soft 404 » vers `/login`)
