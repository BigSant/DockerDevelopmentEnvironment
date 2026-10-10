#!/bin/sh
# Cron service: render PHP drop-ins, build the wrapped crontab, start the UI, then supercronic.
set -eu
sh /opt/setup/runtime/render-templates.sh /usr/local/etc/php/project.d /usr/local/etc/php/project.r \
    /usr/local/etc/php/project.d.env /usr/local/etc/php/project.r.env
mkdir -p "${CRON_HISTORY_DIR:-/var/lib/setup-cron}"
php /opt/setup/runtime/cron/compose.php
php -S 0.0.0.0:8080 -t /opt/setup/runtime/cron/ui /opt/setup/runtime/cron/ui/index.php > /tmp/cron-ui.log 2>&1 &
ui=$!
if [ "${CRON_SCHEDULE:-on}" = off ]; then
    # CRON_SCHEDULE=off: the UI lists the jobs and runs them by hand (recorded as manual); nothing runs on
    # schedule. supercronic still checks the crontab, so a broken one fails here as it would when on.
    supercronic -test /tmp/crontab
    rm -f /tmp/supercronic.pid
    echo "Cron: schedule off (CRON_SCHEDULE=off); jobs run only with \"Run now\" in the UI"
    # The UI holds the container; a crashed UI ends it (restart: unless-stopped). The PHP image stops with
    # SIGQUIT, so that ends it too, without waiting for Docker's kill timeout.
    trap 'kill "$ui" 2>/dev/null; exit 0' TERM INT QUIT
    wait "$ui"
    exit 1
fi
# The UI reloads supercronic (SIGUSR2) after a crontab edit; exec keeps this PID.
echo $$ > /tmp/supercronic.pid
# No -overlapping: a job still running when its next time comes is skipped, not started twice.
exec supercronic /tmp/crontab
