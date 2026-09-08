<#
.SYNOPSIS
    Windows equivalent of scripts/setup_db.sh: creates the focus_finops
    database and focus_app role, then applies the FOCUS v1.4 schema.

.DESCRIPTION
    Run scripts\install_postgres.ps1 first if `psql` isn't already on PATH.
    This script connects as the `postgres` superuser to create the app
    role/database (skipping if they already exist), then invokes the CLI's
    setup-db command to apply schema.sql.

.PARAMETER SuperPassword
    Password for the `postgres` superuser (set during install). Default: "postgres".

.PARAMETER DbName
    Database to create. Default: $env:PGDATABASE or "focus_finops".

.PARAMETER DbUser
    App role to create. Default: $env:PGUSER or "focus_app".

.PARAMETER DbPassword
    Password for the app role. Default: $env:PGPASSWORD or "focus_app_pw".

.EXAMPLE
    .\scripts\setup_db.ps1
#>
[CmdletBinding()]
param(
    [string]$SuperPassword = "postgres",
    [string]$DbName = $(if ($env:PGDATABASE) { $env:PGDATABASE } else { "focus_finops" }),
    [string]$DbUser = $(if ($env:PGUSER) { $env:PGUSER } else { "focus_app" }),
    [string]$DbPassword = $(if ($env:PGPASSWORD) { $env:PGPASSWORD } else { "focus_app_pw" })
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command psql -ErrorAction SilentlyContinue)) {
    Write-Error "psql not found on PATH. Run .\scripts\install_postgres.ps1 first (and open a new PowerShell window)."
    exit 1
}

Write-Host "Creating role/database (skipping if they already exist)..."

$sql = @"
DO `$`$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '$DbUser') THEN
      CREATE ROLE $DbUser WITH LOGIN PASSWORD '$DbPassword';
   END IF;
END
`$`$;
"@

$env:PGPASSWORD = $SuperPassword
$sql | & psql -U postgres -h localhost -v ON_ERROR_STOP=1
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$dbExistsRaw = & psql -U postgres -h localhost -tAc "SELECT 1 FROM pg_database WHERE datname='$DbName'"
$dbExists = if ($dbExistsRaw) { ($dbExistsRaw | Out-String).Trim() } else { "" }
if ($dbExists -ne "1") {
    & psql -U postgres -h localhost -v ON_ERROR_STOP=1 -c "CREATE DATABASE $DbName OWNER $DbUser;"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

& psql -U postgres -h localhost -v ON_ERROR_STOP=1 -c "GRANT ALL PRIVILEGES ON DATABASE $DbName TO $DbUser;"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Remove-Item Env:\PGPASSWORD

Write-Host "Applying schema..."
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $env:PGDATABASE = $DbName
    $env:PGUSER = $DbUser
    $env:PGPASSWORD = $DbPassword
    $env:PYTHONPATH = "src"
    python -m focus_finops.cli setup-db
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}

Write-Host "Done."
