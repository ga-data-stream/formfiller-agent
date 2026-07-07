# Design — Déploiement & exécution chez le collègue (Plan B)

**Date** : 2026-07-07
**Statut** : validé (design)
**Spec liée** : [Traitement à la chaîne + tri](2026-07-07-batch-triage-design.md) (Plan A)

## Problème

La « production » tournera sur le **poste Windows d'un collègue** qui dispose de
la boîte générique recevant les demandes de formulaire. Il faut déployer et
lancer le projet chez lui avec **le moins de friction possible**, sachant que le
collègue n'est pas développeur et que l'installation se fera **une fois**, en
présence de Pierre ou de l'IT.

## Contrainte fondamentale

Le runtime doit être **natif Windows** :
- `OutlookEmailSource` pilote Outlook via **COM (pywin32)** → nécessite Outlook
  desktop installé et une **session interactive ouverte** avec un profil
  connecté.
- Playwright pilote un navigateur Chromium local.

➡️ **Le `.devcontainer/` (Linux) ne peut PAS faire tourner la prod** : un
conteneur Linux n'a aucun accès au COM Outlook du poste. Le devcontainer sert
uniquement au **développement** de Claude Code. On ne le livre pas au collègue.

## Objectif

Un **script d'installation PowerShell** (`install.ps1`) lancé une fois qui rend
le poste opérationnel, plus un **raccourci bureau** pour lancer un batch d'un
double-clic et une **tâche planifiée** de secours. Après install, le collègue
n'a qu'à double-cliquer (ou laisser la tâche tourner).

## Décisions (issues du brainstorming)

| # | Décision | Choix retenu |
|---|----------|--------------|
| 4 | Acheminement des mails | **Redirection** de la boîte générique → dossier dédié de la boîte **principale** du collègue |
| 5 | Installation | Script PowerShell + raccourci (natif Windows) |
| — | Config livrée | Un `config.prod.example.yaml` versionné, ajustable au besoin (jamais le `config.yaml` local de Pierre) |

## Non-objectifs

- **Pas** d'exécutable packagé (PyInstaller) : packager Playwright + pywin32 est
  fragile (alternative écartée).
- **Pas** de vrai service d'arrière-plan / session 0 : incompatible avec le COM
  Outlook (cf. caveat ci-dessous).
- **Pas** d'accès programmatique à la boîte générique (cf. Plan A, non-objectifs).

## Prérequis (runbook — tâches humaines/IT, une seule fois)

1. **Règle de redirection** : les demandes de formulaire arrivant sur la boîte
   générique sont **redirigées** (pas « transférées ») vers l'adresse du
   collègue, puis classées par une règle dans un dossier dédié
   `<Inbox principale>/ligne adressage`. Posée par qui a le droit sur la boîte
   générique (le collègue depuis son Outlook si permis, ou l'IT).
   - **Redirection, pas transfert** : le transfert réécrit l'expéditeur (`FW:`,
     `From` = collègue) alors que `client_name` est dérivé du `sender` ; la
     redirection préserve l'expéditeur d'origine. `client_name` reste un champ de
     log non critique — le **lien du formulaire** survit dans les deux cas.
   - L'outil est **agnostique** au mécanisme : il lit ce qui atterrit dans le
     dossier dédié (règle serveur, règle client, ou glisser-déposer manuel en
     dépannage).
2. **Outlook desktop** installé, collègue **connecté** sur son compte principal.
3. **Python ≥ 3.11** (le script vérifie ; sinon `winget install Python.Python.3.11`).

## Conception

### 1. `install.ps1` (à la racine du repo, lancé une fois)

Étapes idempotentes (ré-exécutable sans casse) :

1. **Vérifier Python ≥ 3.11** ; proposer `winget install` si absent.
2. **Créer un venv** dans le dossier projet (`python -m venv .venv`).
3. **Installer le package** : `.venv\Scripts\pip install -e .` (dépendances
   depuis `pyproject.toml` — règle CLAUDE.md : jamais en dur ailleurs).
4. **Installer le navigateur** : `.venv\Scripts\playwright install chromium`.
5. **Provisionner `.env`** : demander interactivement
   `AZURE_OPENAI_API_KEY` et `AZURE_OPENAI_ENDPOINT` (Pierre les colle), écrire
   `.env`. **Jamais affiché en clair, jamais commité** ; vérifier que `.env` est
   bien dans `.gitignore`. (Règle CLAUDE.md.)
6. **Config de prod** : ⚠️ `config.yaml` est **suivi par git** — au clone, le
   poste hérite du `config.yaml` de dev de Pierre, **pas** de la prod. Le script
   écrit donc `config.yaml` à partir de `config.prod.example.yaml`. S'il existe
   déjà, il en fait une **sauvegarde** (`config.yaml.bak-<horodatage>`) **avant**
   d'écrire, puis le collègue/Pierre ajuste au besoin. (Idempotent : une prod
   déjà ajustée n'est pas silencieusement écrasée — sauvegarde + message.)
   On **ne livre pas** les overrides runtime locaux de Pierre (cf. mémoire projet).
   *Piste d'assainissement (hors périmètre immédiat, à décider en implémentation) :
   `git rm --cached config.yaml` + `config.example.yaml` versionné, pour que
   `config.yaml` ne soit plus suivi.*
7. **Raccourci bureau** → `run-batch.cmd` (voir §2).
8. **Tâche planifiée** de secours (voir §3).
9. **Smoke test** guidé (voir §4).

Le script **n'affiche jamais** les valeurs de secrets et n'appelle aucune commande qui les logue.

### 2. `run-batch.cmd` — lanceur du batch (nouveau, versionné)

```bat
@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
formfiller-batch
echo.
pause
```

- Cible du **raccourci bureau** : double-clic = une passe batch, puis `pause`
  pour que le collègue lise le récap (compteurs + chemins).
- La **tâche planifiée** appelle la même commande **sans** `pause` (variante
  `run-batch-scheduled.cmd`, ou un flag `--no-pause`).

### 3. Tâche planifiée — caveat COM (point le plus délicat)

L'automatisation COM d'Outlook exige une **session interactive avec Outlook
lancé**. Une tâche configurée « exécuter que l'utilisateur soit connecté ou
non » (session 0) **échouera** à piloter Outlook.

Configuration retenue (`Register-ScheduledTask` / `schtasks`) :
- **Déclencheur** : à l'**ouverture de session** du collègue, **répétée toutes
  les N minutes** (ex. 15 min).
- **Condition** : « exécuter **seulement** si l'utilisateur est connecté ».
- **Hypothèse** : Outlook est ouvert pendant les heures de travail (le batch peut
  lancer Outlook via COM s'il est fermé, mais c'est plus fiable s'il tourne
  déjà).

➡️ Le « planifié » est donc un **run périodique en session**, pas un service
d'arrière-plan. C'est la réalité technique assumée et documentée.

### 4. Smoke test (fin d'install, guidé)

1. S'assurer qu'un **mail de test** (avec un vrai lien de formulaire) est dans
   `<Inbox>/ligne adressage`.
2. Option prudente : régler temporairement `dry_run: true` pour la 1ʳᵉ passe.
3. Lancer `run-batch.cmd` ; vérifier : le formulaire est lu, une ligne apparaît
   dans l'Excel, un aperçu/capture est produit, le mail se **déplace** vers
   `Traité` ou `Revue humaine`, le récap s'affiche.
4. Repasser `dry_run: false` une fois la chaîne validée.

### 5. Credentials

- Pierre provisionne `.env` pendant l'install guidée (colle les valeurs).
- **Recommandation** (non bloquante) : une **clé Azure dédiée** à ce poste, pour
  la rotation et la traçabilité.
- `.env` reste **local**, gitignore, jamais logué.

### 6. Mises à jour / maintenance

- `git pull` puis `update.ps1` (léger) : re-`pip install -e .` si les
  dépendances ont changé, `playwright install` si besoin.
- La règle « Rebuild Container » de CLAUDE.md ne concerne **que** le
  devcontainer de dev, **pas** l'install native du collègue.

## `config.prod.example.yaml` (livré, versionné)

Template commenté, valeurs de prod par défaut (à ajuster au besoin) :

```yaml
confidence_threshold: 0.8
dry_run: false                       # prod : auto-soumission au-dessus du seuil
fill_strategy: "agent"
excel_log_path: "./form_log.xlsx"
review_queue_dir: "./review_queue"
inbox_list_count: 140
inbox_subfolder: "ligne adressage"   # dossier dédié dans la boîte PRINCIPALE
processed_subfolder: "Traité"
review_subfolder: "Revue humaine"
batch_lock_path: "./.batch.lock"
processed_ledger_path: "./processed_ids.json"
run_log_dir: "./logs"
azure_openai_deployment: "gpt-5.4-nano"
azure_api_version: "preview"
reasoning_effort: "low"
verifier_model_deployment: "gpt-5.4-mini"
verifier_reasoning_effort: "medium"
```

## Sécurité (règles CLAUDE.md)

- `install.ps1` / `update.ps1` **n'affichent ni ne loguent** les secrets.
- `.env` est **gitignore** → jamais dans le repo ; il est **généré localement**
  par `install.ps1`.
- `config.yaml` est suivi mais **réécrit localement** depuis le template de prod
  (avec sauvegarde) — les overrides runtime locaux de Pierre ne partent pas en
  prod.
- `.devcontainer/` **est** dans le repo mais **non utilisé en prod** (Linux, pas
  de COM Outlook) ; il est simplement ignoré côté poste du collègue.

## Tests / validation

- Le déploiement se valide par le **smoke test** (§4), pas par pytest.
- `install.ps1` doit être **idempotent** : le relancer ne casse pas une install
  existante (venv réutilisé, `.env`/`config.yaml` non écrasés, tâche planifiée
  réenregistrée sans doublon).

## Portée des fichiers

**Nouveaux (versionnés)** : `install.ps1`, `update.ps1`, `run-batch.cmd`,
`run-batch-scheduled.cmd`, `config.prod.example.yaml`, éventuellement une note
runbook `docs/deploiement-collegue.md`.
**Non livrés / non touchés** : `.env`, `config.yaml` local, `.devcontainer/`.

Dépend du Plan A (le script `formfiller-batch` doit exister). Branche
`feat/batch-triage-deployment`.
