#!/usr/bin/env bash
# Restore a backup into a NEW database, then check it. Never restores over a live database.
#   restore.sh <backup file> <new database name>
# Connection as for backup.sh (postgres superuser). Run it where pg_restore is the same
# version as the server, e.g. inside the database container. After restoring, the script runs
# `manage.py verify_database` against the new database if run from the web/ folder.
set -euo pipefail
file="${1:?backup file}"
target="${2:?name for the new database}"
owner="${APP_DB_ROLE:-compliance_app}"
if [ -f "$file.sha256" ]; then
  ( cd "$(dirname "$file")" && sha256sum --check --status "$(basename "$file").sha256" ) \
    || { echo "Checksum does not match: the backup file is damaged" >&2; exit 1; }
fi
if psql -At -d postgres -c "SELECT 1 FROM pg_database WHERE datname = '$target'" | grep -q 1; then
  echo "Database $target already exists; choose a new name" >&2; exit 1
fi
src="$file"
if [[ "$file" == *.gpg ]]; then
  src="$(mktemp)"; trap 'rm -f "$src"' EXIT
  gpg --batch --decrypt --output "$src" "$file"
fi
createdb --owner "$owner" "$target"
pg_restore --exit-on-error --dbname "$target" "$src"
echo "Restored into $target"
if [ -f manage.py ]; then
  PGDATABASE="$target" PGUSER="$owner" PGPASSWORD="${APP_DB_PASSWORD:-${PGPASSWORD:-}}" python manage.py verify_database
fi
