# CA Workbench - Financial Statements (Step 1). Starts the app on http://127.0.0.1:8015
# Usage:  .\run.ps1            start the server
#         .\run.ps1 -Init      install deps (first time), apply migrations, then start
param([switch]$Init, [int]$Port = 8015)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating virtual environment .venv ..."
    python -m venv .venv
    $Init = $true
}
$py = ".\.venv\Scripts\python.exe"

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example. Edit DATABASE_URL (set your postgres password), then run .\run.ps1 again." -ForegroundColor Yellow
    exit 1
}

if ($Init) {
    & $py -m pip install --upgrade pip | Out-Null
    & $py -m pip install -r requirements.txt
    & $py -m scripts.init_db
}

Write-Host "Open http://127.0.0.1:$Port" -ForegroundColor Green
& $py -m uvicorn app.main:app --host 127.0.0.1 --port $Port --reload
