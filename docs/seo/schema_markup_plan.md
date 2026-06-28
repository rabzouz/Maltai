# Plan de données structurées (schema.org) — maltai.fr

## En place (homepage)
- **SoftwareApplication** : nom, catégorie, OS, description, version, licence,
  dépôt, prix (0 EUR). ✔
- **FAQPage** : 6 questions/réponses alignées sur la section FAQ visible. ✔

## Validation
- Test des résultats enrichis Google : https://search.google.com/test/rich-results
- Validator schema.org : https://validator.schema.org/
- Attendu : `SoftwareApplication` et `FAQPage` détectés sans erreur.

## Évolutions possibles (si pertinent)
- **Organization** (logo, sameAs vers GitHub) pour le knowledge panel.
- **BreadcrumbList** si des pages secondaires sont ajoutées.
- **WebSite** + `potentialAction` SearchAction si une recherche publique existe.

> Ne pas ajouter de markup non visible/contredisant le contenu (risque de pénalité).
