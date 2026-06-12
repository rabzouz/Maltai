# MaltaiAI v1.2 — mise a jour complete (tout-en-un)

Ce zip remplace les precedents : code + interface + logo.

## Contenu
1. 7 nouveaux outils agent : notes, todo-list, email (SMTP)
2. Interface style Odysseus : theme sombre/corail, recherche,
   sections sidebar 🛠 Outils / 📝 Notes / ☑ Taches, composer flottant
3. Marque "MaltaiAI" deux tons (sidebar, accueil, page de connexion)
4. Nouveau logo (embleme M + reseau de noeuds) : favicon + icones PWA

## Fichiers a uploader sur GitHub (dossier par dossier)
RACINE      : app.py
routes/     : notes.py (NOUVEAU)
src/        : tools.py
core/       : config.py + database.py
static/     : index.html + style.css + login.html
              + logo-emblem.png + favicon.png + icon-180.png
              + icon-192.png + icon-512.png + logo-full.png
static/js/  : app.js

Sur github.com/rabzouz/Maltai : ouvre chaque dossier
-> Add file > Upload files -> glisse les fichiers -> Commit changes.

Puis Coolify : Deploy. Navigateur : Ctrl+F5.
PWA telephone : reinstalle l'app pour la nouvelle icone.

## Activer l'outil email_send (optionnel) — variables d'env Coolify
SMTP_HOST=smtp.gmail.com / SMTP_PORT=587 / SMTP_USER=ton@gmail.com /
SMTP_PASSWORD=mot-de-passe-application (myaccount.google.com/apppasswords)
