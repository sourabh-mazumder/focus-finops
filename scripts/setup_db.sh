#!/usr/bin/env bash
# Creates the focus_finops database and focus_app role, then applies the
# FOCUS v1.4 schema. Run once per environment. Assumes you can connect as
# a Postgres superuser (locally: `sudo -u postgres psql`).
#
# Usage: ./scripts/setup_db.sh
set -euo pipefail

DB_NAME="${PGDATABASE:-focus_finops}"
DB_USER="${PGUSER:-focus_app}"
DB_PASSWORD="${PGPASSWORD:-focus_app_pw}"

echo "Creating role/database (skipping if they already exist)..."
sudo -u postgres psql -v ON_ERROR_STOP=0 <<SQL
CREATE ROLE ${DB_USER} WITH LOGIN PASSWORD '${DB_PASSWORD}';
CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};
GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};
SQL

echo "Applying schema..."
cd "$(dirname "$0")/.."
PYTHONPATH=src python3 -m focus_finops.cli setup-db

echo "Done."
