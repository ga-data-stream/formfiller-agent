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
