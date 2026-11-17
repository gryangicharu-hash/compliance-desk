#!/usr/bin/env bash
# Nightly database backup. Run as a user that can connect as the postgres superuser
# (row-level security hides some tables from the application role, so the app's own
# login cannot take a complete backup).
#   PGHOST PGPORT PGUSER PGPASSWORD   connection (PGUSER=postgres)
#   PGDATABASE                        default compliance_desk
#   BACKUP_DIR                        where backups go (put it on separate storage)
#   BACKUP_GPG_RECIPIENT              optional: encrypt each backup to this GPG key
#   KEEP_DAYS                         default 35
#   DOCUMENT_DIR                      optional: KYC document folder to archive alongside
set -euo pipefail
: "${BACKUP_DIR:?set BACKUP_DIR}"
db="${PGDATABASE:-compliance_desk}"
keep="${KEEP_DAYS:-35}"
mkdir -p "$BACKUP_DIR"
umask 077
stamp="$(date +%Y%m%d-%H%M%S)"
file="$BACKUP_DIR/compliance-desk-$stamp.dump"

pg_dump --format=custom --compress=6 --file "$file.partial" "$db"
mv "$file.partial" "$file"
if [ -n "${BACKUP_GPG_RECIPIENT:-}" ]; then
  gpg --batch --yes --encrypt --recipient "$BACKUP_GPG_RECIPIENT" --output "$file.gpg" "$file"
  rm -f "$file"
  file="$file.gpg"
fi
( cd "$BACKUP_DIR" && sha256sum "$(basename "$file")" > "$(basename "$file").sha256" )
if [ -n "${DOCUMENT_DIR:-}" ] && [ -d "$DOCUMENT_DIR" ]; then
  # The files are already encrypted with the field key; the archive only bundles them.
  docs="$BACKUP_DIR/compliance-desk-$stamp.documents.tar"
  tar -C "$DOCUMENT_DIR" -cf "$docs" .
  ( cd "$BACKUP_DIR" && sha256sum "$(basename "$docs")" > "$(basename "$docs").sha256" )
  echo "$docs"
fi
find "$BACKUP_DIR" -name 'compliance-desk-*' -type f -mtime +"$keep" -delete
echo "$file"
