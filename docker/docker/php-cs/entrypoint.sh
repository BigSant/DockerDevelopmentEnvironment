#!/bin/sh

# Copy .php-cs-fixer.php to mounted volume if it doesn't exist yet
if [ ! -f /tmp/php-cs-fixer/config/.php-cs-fixer.php ]; then
    cp /opt/php-cs-fixer/.php-cs-fixer.php /tmp/php-cs-fixer/config/.php-cs-fixer.php
    echo "[init] Generated .php-cs-fixer.php in /tmp/php-cs-fixer/config/"
fi

exec php-cs-fixer --config=/tmp/php-cs-fixer/config/.php-cs-fixer.php "$@"
