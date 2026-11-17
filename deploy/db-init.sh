#!/bin/sh
# Runs once when the database volume is first created. The application connects as
# compliance_app: it owns its tables but is NOT a superuser, so row-level security
# (cases, alerts, audit papers) applies to it. Backups use the postgres superuser.
set -e
psql -v ON_ERROR_STOP=1 --username postgres --dbname postgres <<SQL
CREATE ROLE compliance_app LOGIN PASSWORD '${APP_DB_PASSWORD}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE DATABASE compliance_desk OWNER compliance_app;
REVOKE ALL ON DATABASE compliance_desk FROM PUBLIC;
SQL
