#!/usr/bin/env bash
# Interactive DB import / (re)create / init for a project.
#
# Invoked by the central Makefile's `db-init` target after it has generated the
# merged env file and the compose file. Instead of taking database / drop / init
# as make parameters, it PROMPTS for them (press Enter to accept the default).
#
# Inputs (from the Makefile, via env):
#   ENV                       local|prod|stage
#   SQL_DUMP_FILE             optional dump to import (empty = just create/init)
#   PROJECT_CONFIG_DIRECTORY  project app/config dir (holds sql/init.sql etc.)
# Optional overrides: ENV_FILE (default /tmp/.env), COMPOSE_FILE (default /tmp/docker-compose.yml)
set -euo pipefail

ENV="${ENV:-local}"
SQL_DUMP_FILE="${SQL_DUMP_FILE:-}"
PROJECT_CONFIG_DIRECTORY="${PROJECT_CONFIG_DIRECTORY:-}"
ENV_FILE="${ENV_FILE:-/tmp/.env}"
COMPOSE_FILE="${COMPOSE_FILE:-/tmp/docker-compose.yml}"

set -a; . "$ENV_FILE"; set +a
: "${DATABASE_USER:?DATABASE_USER missing in $ENV_FILE}"
DATABASE_PASSWORD="${DATABASE_PASSWORD:-}"
DATABASE_NAME="${DATABASE_NAME:-}"

compose()   { docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"; }
mysql_run() { compose exec -T database mysql --user="$DATABASE_USER" --password="$DATABASE_PASSWORD" --get-server-public-key "$@"; }

# ── Prompts (Enter = default). EOF/non-interactive falls back to the default. ──
read -rp "Target database [${DATABASE_NAME}]: " target_db || true
target_db="${target_db:-$DATABASE_NAME}"
[ -n "$target_db" ] || { echo "No database name given." >&2; exit 1; }

read -rp "Drop & recreate \"${target_db}\" first? [Y/n]: " drop_ans || true
read -rp "Run init SQL (init.sql + init.${ENV}.sql) after import? [Y/n]: " init_ans || true

# ── (Re)create — DEFAULT is drop & recreate (Enter = yes); type n to keep data ──
case "${drop_ans:-}" in
  [nN]*) echo "==> Ensuring database exists (no drop): ${target_db}"
         mysql_run -e "CREATE DATABASE IF NOT EXISTS \`${target_db}\`;" ;;
  *)     echo "==> Dropping and recreating: ${target_db}"
         mysql_run -e "DROP DATABASE IF EXISTS \`${target_db}\`; CREATE DATABASE \`${target_db}\`;" ;;
esac

# ── Import dump (optional) ──────────────────────────────────────────────────────
if [ -n "$SQL_DUMP_FILE" ]; then
  [ -r "$SQL_DUMP_FILE" ] || { echo "Dump not readable: $SQL_DUMP_FILE" >&2; exit 1; }
  echo "==> Uploading dump: ${SQL_DUMP_FILE}"
  if command -v pv >/dev/null 2>&1; then
    pv "$SQL_DUMP_FILE" | mysql_run "$target_db"
  else
    mysql_run "$target_db" < "$SQL_DUMP_FILE"
  fi
else
  echo "==> No dump file given (pass file=path to import one); skipping import."
fi

# ── Init SQL (optional) ─────────────────────────────────────────────────────────
case "${init_ans:-}" in
  [nN]*) echo "==> Skipping init SQL." ;;
  *)
    for f in "$PROJECT_CONFIG_DIRECTORY/sql/init.sql" "$PROJECT_CONFIG_DIRECTORY/sql/init.${ENV}.sql"; do
      if [ -s "$f" ]; then
        echo "==> Running $(basename "$f")"
        envsubst < "$f" | mysql_run "$target_db"
      fi
    done ;;
esac

echo "==> Done."
