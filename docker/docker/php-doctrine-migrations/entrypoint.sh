#!/bin/sh

VERSIONS_DIR=/tmp/doctrine-migrations/config/versions

# Ensure versions directory exists (migration class files are stored here)
if [ ! -d "${VERSIONS_DIR}" ]; then
    mkdir -p "${VERSIONS_DIR}"
    echo "[init] Created versions directory at ${VERSIONS_DIR}"
fi

exec doctrine-migrations \
    --configuration="/opt/doctrine-migrations/doctrine-migrations.php" \
    --db-configuration="/opt/doctrine-migrations/migrations-db.php" \
    "$@"
