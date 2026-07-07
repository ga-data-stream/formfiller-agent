# Plan B — Déploiement & exécution chez le collègue — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> ⚠️ **Ce plan n'a pas de tests pytest** : c'est de l'outillage de déploiement (scripts PowerShell/CMD + template de config). La validation se fait par des **vérifications manuelles** explicites à chaque tâche et par le **smoke test** final. Le vrai runtime exige Outlook + Azure sur un poste Windows.

**Goal:** Rendre un poste Windows opérationnel pour lancer le batch (`formfiller-batch`) d'un double-clic et via une tâche planifiée de secours, avec un `install.ps1` guidé lancé une seule fois.

**Architecture:** Un script `install.ps1` provisionne l'environnement natif Windows (venv, Playwright, `.env`, `config.yaml` de prod), crée un raccourci vers `run-batch.cmd`, et enregistre une tâche planifiée en session. Les mails arrivent dans la boîte principale du collègue via une règle de redirection (prérequis humain). Le devcontainer n'est pas utilisé en prod.

**Tech Stack:** PowerShell 5.1+, Windows Task Scheduler, Python venv, Playwright.

**Dépendance :** le script console `formfiller-batch` doit exister → **Plan A terminé** (`2026-07-07-batch-triage.md`).

## Global Constraints

- Runtime **natif Windows** : Outlook desktop installé + session interactive ouverte ; le `.devcontainer/` (Linux) **ne peut pas** faire tourner la prod (pas de COM Outlook).
- **Ne jamais afficher ni loguer** `AZURE_OPENAI_API_KEY` / `AZURE_OPENAI_ENDPOINT` ; `.env` reste local et gitignore.
- **Ne jamais committer** `.env` ni le `config.yaml` de prod généré sur le poste.
- Dépendances applicatives dans `pyproject.toml` uniquement.
- `install.ps1` doit être **idempotent** (ré-exécutable sans casse : venv réutilisé, `.env`/`config.yaml` non écrasés silencieusement, tâche planifiée sans doublon).
- Branche `feat/batch-triage-deployment`.
- La tâche planifiée s'exécute **uniquement en session interactive** (contrainte COM Outlook).

---

### Task 1: Template de config de prod + lanceurs CMD

**Files:**
- Create: `config.prod.example.yaml`
- Create: `run-batch.cmd`
- Create: `run-batch-scheduled.cmd`

**Interfaces:**
- Produces: un template de config prod versionné ; deux lanceurs (interactif avec `pause`, planifié sans `pause`) que la tâche planifiée et le raccourci appelleront.

- [ ] **Step 1: Créer `config.prod.example.yaml`** (racine du repo)

```yaml
# Config de PRODUCTION (poste du collègue). Copiée en config.yaml par install.ps1.
# Ajustable au besoin. NE PAS committer le config.yaml généré sur le poste.
confidence_threshold: 0.8
dry_run: false                       # prod : auto-soumission au-dessus du seuil
fill_strategy: "agent"
excel_log_path: "./form_log.xlsx"
review_queue_dir: "./review_queue"
inbox_list_count: 140
inbox_subfolder: "ligne adressage"   # dossier dédié dans la boîte PRINCIPALE du collègue
processed_subfolder: "Traité"
review_subfolder: "Revue humaine"
batch_lock_path: "./.batch.lock"
lock_stale_seconds: 3600
processed_ledger_path: "./processed_ids.json"
run_log_dir: "./logs"
azure_openai_deployment: "gpt-5.4-nano"
azure_api_version: "preview"
reasoning_effort: "low"
verifier_model_deployment: "gpt-5.4-mini"
verifier_reasoning_effort: "medium"
```

- [ ] **Step 2: Créer `run-batch.cmd`** (lanceur interactif, cible du raccourci bureau)

```bat
@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
formfiller-batch
echo.
pause
```

- [ ] **Step 3: Créer `run-batch-scheduled.cmd`** (lanceur planifié, sans pause)

```bat
@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
formfiller-batch
```

- [ ] **Step 4: Vérifier**

- `config.prod.example.yaml` contient bien tous les champs consommés par le Plan A (`processed_subfolder`, `review_subfolder`, `batch_lock_path`, `lock_stale_seconds`, `processed_ledger_path`, `run_log_dir`, `inbox_subfolder`).
- Les deux `.cmd` diffèrent uniquement par la ligne `pause`.

- [ ] **Step 5: Commit**

```bash
git add config.prod.example.yaml run-batch.cmd run-batch-scheduled.cmd
git commit -m "chore(deploy): template config prod + lanceurs batch"
```

---

### Task 2: `install.ps1` — environnement + `.env` + config

**Files:**
- Create: `install.ps1`

**Interfaces:**
- Consumes: `config.prod.example.yaml` (Task 1) ; `pyproject.toml`.
- Produces: `.venv/`, `.env`, `config.yaml` (prod), navigateur Playwright installés sur le poste.

- [ ] **Step 1: Écrire la partie environnement de `install.ps1`**

```powershell
# install.ps1 — provisionne le poste du collègue (à lancer une fois, avec Pierre/IT).
# N'AFFICHE JAMAIS les secrets. Idempotent.
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# 1. Python >= 3.11
$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) { throw "Python introuvable. Installez Python >= 3.11 (winget install Python.Python.3.11) puis relancez." }
$ver = (python -c "import sys; print('%d.%d' % sys.version_info[:2])")
if ([version]$ver -lt [version]"3.11") { throw "Python $ver detecte ; 3.11+ requis." }
Write-Host "Python $ver OK."

# 2. venv (reutilise s'il existe deja)
if (-not (Test-Path ".venv")) { python -m venv .venv }
$pip = ".\.venv\Scripts\pip.exe"

# 3. Installer le package + deps (depuis pyproject.toml)
& $pip install -e .

# 4. Navigateur Playwright
& ".\.venv\Scripts\playwright.exe" install chromium
```

- [ ] **Step 2: Ajouter le provisionnement `.env` (jamais affiché)**

```powershell
# 5. .env — provisionne les credentials Azure sans les afficher.
if (Test-Path ".env") {
    Write-Host ".env existe deja — conserve (aucun secret affiche)."
} else {
    $key = Read-Host "AZURE_OPENAI_API_KEY" -AsSecureString
    $endpoint = Read-Host "AZURE_OPENAI_ENDPOINT"
    $keyPlain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($key))
    Set-Content -Path ".env" -Encoding UTF8 -Value @(
        "AZURE_OPENAI_API_KEY=$keyPlain",
        "AZURE_OPENAI_ENDPOINT=$endpoint"
    )
    $keyPlain = $null
    Write-Host ".env cree (valeurs non affichees)."
}
```

- [ ] **Step 3: Ajouter le provisionnement de `config.yaml` (avec sauvegarde)**

```powershell
# 6. config.yaml de prod. config.yaml est suivi par git → au clone, le poste a la
#    config de DEV de Pierre. On ecrit la prod depuis le template ; si un config.yaml
#    existe deja, on le SAUVEGARDE avant (pas d'ecrasement silencieux).
if (Test-Path "config.yaml") {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    Copy-Item "config.yaml" "config.yaml.bak-$stamp"
    Write-Host "config.yaml existant sauvegarde en config.yaml.bak-$stamp."
}
Copy-Item "config.prod.example.yaml" "config.yaml" -Force
Write-Host "config.yaml de prod ecrit (ajustez si besoin)."
```

- [ ] **Step 4: Vérifier (sur un poste Windows de test, ou le poste cible)**

- Lancer `./install.ps1` ; répondre aux prompts `.env`.
- Vérifier : `.venv/` existe ; `./.venv/Scripts/pip.exe show formfiller` liste le package ; `config.yaml` = contenu du template ; `.env` existe et **n'a pas** été affiché dans la console.
- Vérifier l'idempotence : relancer `./install.ps1` → `.env` conservé, `config.yaml` sauvegardé puis réécrit, aucune erreur.
- Confirmer que `.env` est bien ignoré : `git status` ne doit pas lister `.env`.

- [ ] **Step 5: Commit**

```bash
git add install.ps1
git commit -m "chore(deploy): install.ps1 (venv, playwright, .env, config prod)"
```

---

### Task 3: Raccourci bureau + tâche planifiée en session

**Files:**
- Modify: `install.ps1` (ajout raccourci + tâche planifiée + rappel smoke test)

**Interfaces:**
- Consumes: `run-batch.cmd`, `run-batch-scheduled.cmd` (Task 1) ; `.venv` (Task 2).
- Produces: un raccourci `.lnk` sur le Bureau ; une tâche planifiée `FormfillerBatch` déclenchée à l'ouverture de session, répétée toutes les 15 min, **en session interactive uniquement**.

- [ ] **Step 1: Ajouter la création du raccourci bureau à `install.ps1`**

```powershell
# 7. Raccourci bureau -> run-batch.cmd
$desktop = [Environment]::GetFolderPath("Desktop")
$lnk = Join-Path $desktop "Formfiller - Traiter les demandes.lnk"
$wsh = New-Object -ComObject WScript.Shell
$sc = $wsh.CreateShortcut($lnk)
$sc.TargetPath = Join-Path $PSScriptRoot "run-batch.cmd"
$sc.WorkingDirectory = $PSScriptRoot
$sc.Save()
Write-Host "Raccourci bureau cree : $lnk"
```

- [ ] **Step 2: Ajouter l'enregistrement de la tâche planifiée (en session)**

```powershell
# 8. Tache planifiee : a l'ouverture de session, repetee toutes les 15 min,
#    UNIQUEMENT si l'utilisateur est connecte (COM Outlook exige une session
#    interactive avec Outlook ouvert — pas de session 0 / service).
$taskName = "FormfillerBatch"
$action = New-ScheduledTaskAction -Execute (Join-Path $PSScriptRoot "run-batch-scheduled.cmd")
$trigger = New-ScheduledTaskTrigger -AtLogOn
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 15)).Repetition
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Principal $principal -Force | Out-Null
Write-Host "Tache planifiee '$taskName' enregistree (a l'ouverture de session + toutes les 15 min, en session)."

Write-Host ""
Write-Host "=== Installation terminee ==="
Write-Host "Prochaine etape : SMOKE TEST (voir Task 4 du plan de deploiement)."
```

- [ ] **Step 3: Vérifier (poste cible)**

- Relancer `./install.ps1`.
- Vérifier le raccourci sur le Bureau (« Formfiller - Traiter les demandes »), cible = `run-batch.cmd`.
- `Get-ScheduledTask -TaskName FormfillerBatch` : existe, principal `Interactive`, déclencheur à l'ouverture de session + répétition 15 min.
- Idempotence : relancer → `Register-ScheduledTask -Force` remplace sans doublon.

- [ ] **Step 4: Commit**

```bash
git add install.ps1
git commit -m "chore(deploy): raccourci bureau + tache planifiee en session"
```

---

### Task 4: Smoke test guidé + runbook

**Files:**
- Create: `docs/deploiement-collegue.md` (runbook : prérequis + smoke test + dépannage)

**Interfaces:**
- Consumes: tout ce qui précède (Plan A installé, `install.ps1` exécuté).

- [ ] **Step 1: Rédiger `docs/deploiement-collegue.md`**

Contenu (runbook) :

```markdown
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
- Automatique : la tâche planifiée relance toutes les 15 min **tant que la
  session est ouverte et Outlook lancé** (ce n'est pas un service d'arrière-plan).

## Dépannage
- « Un batch est déjà en cours » : un verrou `.batch.lock` est présent ; il
  s'auto-périme après 1 h, ou supprimez-le si aucun run n'est actif.
- Rien ne se traite : vérifier que les mails arrivent bien dans
  `ligne adressage` (règle de redirection) et qu'Outlook est ouvert.
- Un mail « traité mais non déplacé » (récap) : à ranger manuellement ; il ne
  sera pas retraité (présent au registre `processed_ids.json`).

## Mises à jour
- `git pull` puis `./update.ps1` (voir Task 5).
```

- [ ] **Step 2: Vérifier**

Relire le runbook : prérequis, install, smoke test, quotidien, dépannage, MAJ sont tous présents et cohérents avec `config.prod.example.yaml`.

- [ ] **Step 3: Commit**

```bash
git add docs/deploiement-collegue.md
git commit -m "docs(deploy): runbook + smoke test poste collègue"
```

---

### Task 5: `update.ps1` — mises à jour

**Files:**
- Create: `update.ps1`

**Interfaces:**
- Consumes: `.venv` (Task 2).
- Produces: réinstallation légère après un `git pull`.

- [ ] **Step 1: Écrire `update.ps1`**

```powershell
# update.ps1 — a lancer apres 'git pull'. Idempotent, n'affiche aucun secret,
# ne touche NI .env NI config.yaml.
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if (-not (Test-Path ".venv")) { throw "Pas de .venv — lancez d'abord install.ps1." }
$pip = ".\.venv\Scripts\pip.exe"

# Re-installe le package (deps depuis pyproject.toml si elles ont change).
& $pip install -e .
# S'assure que le navigateur est present (no-op s'il l'est deja).
& ".\.venv\Scripts\playwright.exe" install chromium

Write-Host "Mise a jour terminee. .env et config.yaml inchanges."
```

- [ ] **Step 2: Vérifier (poste de test)**

- `./update.ps1` sur un poste sans `.venv` → erreur claire « lancez d'abord install.ps1 ».
- Avec `.venv` → réinstalle sans toucher `.env`/`config.yaml` (vérifier leurs dates de modif inchangées).

- [ ] **Step 3: Commit**

```bash
git add update.ps1
git commit -m "chore(deploy): update.ps1 pour les mises a jour"
```

---

## Self-Review

**Spec coverage** (contre `2026-07-07-deployment-colleague-workstation-design.md`) :
- Contrainte runtime natif / devcontainer exclu → Global Constraints + runbook. ✅
- Prérequis (redirection, Outlook, Python) → runbook Task 4. ✅
- `install.ps1` (Python, venv, pip, playwright, `.env`, config, raccourci, tâche, smoke) → Tasks 2 + 3 (smoke = runbook Task 4). ✅
- `run-batch.cmd` + variante planifiée → Task 1. ✅
- Caveat COM / tâche en session interactive → Task 3 (principal `Interactive`, `-AtLogOn`) + runbook. ✅
- Smoke test → Task 4. ✅
- Credentials (non affichés, `.env` local) → Task 2 Step 2. ✅
- `config.yaml` suivi par git → réécrit avec sauvegarde → Task 2 Step 3. ✅
- Mises à jour / pas de « Rebuild Container » en prod → Task 5 + runbook. ✅
- `config.prod.example.yaml` → Task 1. ✅
- Idempotence de l'install → vérifs Tasks 2 & 3. ✅

**Placeholder scan** : aucun TBD/TODO ; chaque script est complet.

**Type/nom consistency** : noms de dossiers Outlook (`Traité`/`Revue humaine`), champs de config et chemins **identiques** à ceux du Plan A. Nom de tâche `FormfillerBatch`, script console `formfiller-batch`, lanceurs `run-batch.cmd`/`run-batch-scheduled.cmd` cohérents entre tâches.

## Notes d'exécution

- **Ne pas committer** le `.env` ni le `config.yaml` généré sur le poste.
- La règle de redirection est un prérequis **humain** (hors code) : à confirmer avec le collègue/IT avant le smoke test.
- Le « planifié » est un run périodique **en session**, jamais un service (contrainte COM Outlook) — documenté et assumé.
