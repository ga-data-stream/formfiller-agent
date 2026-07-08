# install.ps1 — provisionne le poste du collègue (à lancer une fois, avec Pierre/IT).
# N'AFFICHE JAMAIS les secrets. Idempotent.
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# 1. Python >= 3.11
$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) { throw "Python introuvable. Installez Python >= 3.11 (winget install Python.Python.3.11) puis relancez." }
$ver = (python -c "import sys; print('%d.%d' % sys.version_info[:2])")
if ([string]::IsNullOrWhiteSpace($ver)) { throw "Python introuvable ou non fonctionnel (alias Windows ?). Installez Python >= 3.11 puis relancez." }
if ([version]$ver -lt [version]"3.11") { throw "Python $ver detecte ; 3.11+ requis." }
Write-Host "Python $ver OK."

# 2. venv (reutilise s'il existe deja)
if (-not (Test-Path ".venv")) { python -m venv .venv }
$pip = ".\.venv\Scripts\pip.exe"

# 3. Installer le package + deps (depuis pyproject.toml)
& $pip install -e .

# 4. Navigateur Playwright
& ".\.venv\Scripts\playwright.exe" install chromium

# 5. .env — provisionne les credentials Azure sans les afficher.
if (Test-Path ".env") {
    Write-Host ".env existe deja — conserve (aucun secret affiche)."
} else {
    $key = Read-Host "AZURE_OPENAI_API_KEY" -AsSecureString
    $endpoint = Read-Host "AZURE_OPENAI_ENDPOINT" -AsSecureString
    $keyBstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($key)
    $endBstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($endpoint)
    try {
        $keyPlain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyBstr)
        $endPlain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($endBstr)
        # UTF-8 SANS BOM : python-dotenv ne retire pas le BOM et lirait sinon la
        # 1re cle comme "﻿AZURE_OPENAI_API_KEY" -> KeyError au 1er run.
        [System.IO.File]::WriteAllLines(
            (Join-Path $PSScriptRoot ".env"),
            [string[]]@("AZURE_OPENAI_API_KEY=$keyPlain", "AZURE_OPENAI_ENDPOINT=$endPlain"),
            (New-Object System.Text.UTF8Encoding($false)))
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyBstr)
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($endBstr)
        $keyPlain = $null; $endPlain = $null
    }
    Write-Host ".env cree (valeurs non affichees, sans BOM)."
}

# 6. config.yaml de prod. config.yaml est suivi par git → au clone, le poste a la
#    config de DEV de Pierre. On ecrit la prod depuis le template ; si un config.yaml
#    existe deja, on le SAUVEGARDE avant (pas d'ecrasement silencieux).
if (Test-Path "config.yaml") {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    Copy-Item "config.yaml" "config.yaml.bak-$stamp" -Force
    Write-Host "config.yaml existant sauvegarde en config.yaml.bak-$stamp."
}
Copy-Item "config.prod.example.yaml" "config.yaml" -Force
Write-Host "config.yaml de prod ecrit (ajustez si besoin)."

# 7. Raccourci bureau -> run-batch.cmd
$desktop = [Environment]::GetFolderPath("Desktop")
$lnk = Join-Path $desktop "Formfiller - Traiter les demandes.lnk"
$wsh = New-Object -ComObject WScript.Shell
$sc = $wsh.CreateShortcut($lnk)
$sc.TargetPath = Join-Path $PSScriptRoot "run-batch.cmd"
$sc.WorkingDirectory = $PSScriptRoot
$sc.Save()
Write-Host "Raccourci bureau cree : $lnk"

Write-Host ""
Write-Host "=== Installation terminee ==="
Write-Host "Prochaine etape : SMOKE TEST (voir docs/deploiement-collegue.md)."
Write-Host "Tache planifiee non installee pour le moment (a activer plus tard, hors perimetre)."
