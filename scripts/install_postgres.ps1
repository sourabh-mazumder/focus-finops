<#
.SYNOPSIS
    Installs PostgreSQL on Windows (via winget, falling back to Chocolatey)
    and makes sure `psql` ends up on PATH. Run once per machine.

.DESCRIPTION
    focus-finops talks to Postgres by shelling out to `psql` (see
    src/focus_finops/db.py), so this script's only job is to get a working
    PostgreSQL server + client tools installed and `psql` discoverable on
    PATH. After this, run scripts\setup_db.ps1 to create the focus_finops
    database/role and apply the schema.

.PARAMETER Version
    PostgreSQL major version to install. Default: 16.

.PARAMETER SuperPassword
    Password to set for the `postgres` superuser during install. Default:
    "postgres" -- change this for anything beyond local dev.

.PARAMETER Port
    Port for the PostgreSQL service. Default: 5432.

.EXAMPLE
    # From an elevated PowerShell prompt:
    .\scripts\install_postgres.ps1

.EXAMPLE
    .\scripts\install_postgres.ps1 -Version 16 -SuperPassword "s3cret" -Port 5432
#>
[CmdletBinding()]
param(
    [string]$Version = "16",
    [string]$SuperPassword = "postgres",
    [int]$Port = 5432
)

$ErrorActionPreference = "Stop"

function Test-Command($name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

# --- Already installed? -----------------------------------------------
if (Test-Command "psql") {
    $existing = (Get-Command psql).Source
    Write-Host "psql already on PATH: $existing"
    & psql --version
    Write-Host "Skipping install. Delete/rename that psql if you want this script to install a fresh copy."
    exit 0
}

# --- Elevation check -----------------------------------------------
$currentPrincipal = New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent()
)
if (-not $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Warning "Not running as Administrator. The installer may fail to register the Windows service. Re-run this script from an elevated PowerShell prompt if you hit issues."
}

$installed = $false

# --- Try winget first -----------------------------------------------
if (Test-Command "winget") {
    Write-Host "Installing PostgreSQL $Version via winget..."
    $wingetId = "PostgreSQL.PostgreSQL.$Version"
    $overrideArgs = "--mode unattended --unattendedmodeui minimal --superpassword $SuperPassword --serverport $Port"

    winget install --exact --id $wingetId `
        --silent `
        --accept-package-agreements --accept-source-agreements `
        --override $overrideArgs

    if ($LASTEXITCODE -eq 0) {
        $installed = $true
    } else {
        Write-Warning "winget install failed (exit $LASTEXITCODE). Trying Chocolatey..."
    }
} else {
    Write-Host "winget not found. Trying Chocolatey..."
}

# --- Fall back to Chocolatey -----------------------------------------------
if (-not $installed) {
    if (Test-Command "choco") {
        Write-Host "Installing PostgreSQL $Version via Chocolatey..."
        choco install "postgresql$Version" -y `
            --params "/Password:$SuperPassword /Port:$Port"
        if ($LASTEXITCODE -eq 0) {
            $installed = $true
        } else {
            Write-Warning "choco install failed (exit $LASTEXITCODE)."
        }
    } else {
        Write-Host "Chocolatey not found either."
    }
}

if (-not $installed) {
    Write-Error @"
Could not install PostgreSQL automatically (neither winget nor choco succeeded/available).

Install manually instead:
  1. Download the installer from https://www.postgresql.org/download/windows/
  2. Run it, set the postgres superuser password, keep the default port ($Port).
  3. Add the 'bin' folder (e.g. C:\Program Files\PostgreSQL\$Version\bin) to your PATH.
  4. Re-open PowerShell and confirm with: psql --version
"@
    exit 1
}

# --- Locate psql.exe and add it to PATH -----------------------------------------------
$candidateDirs = Get-ChildItem "C:\Program Files\PostgreSQL" -Directory -ErrorAction SilentlyContinue |
    Sort-Object Name -Descending

$psqlDir = $null
foreach ($dir in $candidateDirs) {
    $binPath = Join-Path $dir.FullName "bin"
    if (Test-Path (Join-Path $binPath "psql.exe")) {
        $psqlDir = $binPath
        break
    }
}

if (-not $psqlDir) {
    Write-Warning "Install reported success but couldn't find psql.exe under C:\Program Files\PostgreSQL. Add its bin directory to PATH manually."
    exit 1
}

Write-Host "Found psql at: $psqlDir"

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$psqlDir*") {
    Write-Host "Adding $psqlDir to your User PATH..."
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$psqlDir", "User")
}

# Make it usable in this session too, without reopening PowerShell.
$env:Path = "$env:Path;$psqlDir"

Write-Host ""
Write-Host "PostgreSQL $Version installed."
& "$psqlDir\psql.exe" --version
Write-Host ""
Write-Host "postgres superuser password: $SuperPassword"
Write-Host "Open a NEW PowerShell window (so the updated PATH takes effect everywhere), then run:"
Write-Host "  .\scripts\setup_db.ps1"
