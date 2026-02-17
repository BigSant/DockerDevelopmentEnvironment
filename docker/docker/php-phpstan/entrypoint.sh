#!/bin/sh

# Copy phpstan.neon to mounted volume if it doesn't exist yet
if [ ! -f /tmp/phpstan/config/phpstan.neon ]; then
    cp /opt/phpstan/phpstan.neon /tmp/phpstan/config/phpstan.neon
    echo "[init] Generated phpstan.neon in /tmp/phpstan/config/"
fi

exec phpstan analyse --configuration=/tmp/phpstan/config/phpstan.neon "$@"
