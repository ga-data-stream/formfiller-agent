# Runbook — Déploiement Formfiller chez le collègue

## Prérequis (une fois, humain/IT)
1. Règle de **redirection** (pas transfert) de la boîte générique → adresse du
   collègue, puis règle côté collègue classant ces mails dans
   `<Inbox principale>/ligne adressage`. (Le transfert réécrit l'expéditeur ; la
   redirection le préserve.)
2. Outlook desktop installé, collègue connecté sur son compte principal.
3. Python ≥ 3.11 (sinon `winget install Python.Python.3.11`).

## Installation
1. Cloner/copier le repo sur le poste.
2. Ouvrir PowerShell dans le dossier, lancer `./install.ps1`.
3. Coller la clé et l'endpoint Azure quand demandé (non affichés).

## Smoke test (à faire juste après l'install)
1. Mettre `dry_run: true` dans `config.yaml` (1ʳᵉ passe prudente).
2. Placer un mail de test contenant un vrai lien de formulaire dans
   `<Inbox>/ligne adressage`.
3. Double-cliquer le raccourci « Formfiller - Traiter les demandes ».
4. Vérifier : une ligne dans `form_log.xlsx`, un aperçu dans `dry_run_preview/`,
   le mail **déplacé** vers `Traité` ou `Revue humaine`, un log dans `logs/`,
   le récap affiché avant `pause`.
5. Repasser `dry_run: false` une fois la chaîne validée de bout en bout.

## Fonctionnement quotidien
- Manuel : double-clic sur le raccourci → une passe, récap affiché.
- Automatique (tâche planifiée) : **à activer plus tard — non installé pour le
  moment**.

## Dépannage
- « Un batch est déjà en cours » : un verrou `.batch.lock` est présent ; il
  s'auto-périme après 1 h, ou supprimez-le si aucun run n'est actif.
- Rien ne se traite : vérifier que les mails arrivent bien dans
  `ligne adressage` (règle de redirection) et qu'Outlook est ouvert.
- Un mail « traité mais non déplacé » (récap) : à ranger manuellement ; il ne
  sera pas retraité (présent au registre `processed_ids.json`).

## Mises à jour
- `git pull` puis `./update.ps1` (voir Task 5).
