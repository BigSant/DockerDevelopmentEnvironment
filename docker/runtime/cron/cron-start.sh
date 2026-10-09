#!/bin/sh
# Cron service: render PHP drop-ins, build the wrapped crontab, start the UI, then supercronic.
set -eu
sh /opt/setup/runtime/render-templates.sh /usr/local/etc/php/project.d /usr/local/etc/php/project.r \
    /usr/local/etc/php/project.d.env /usr/local/etc/php/project.r.env
mkdir -p "${CRON_HISTORY_DIR:-/var/lib/setup-cron}"
php /opt/setup/runtime/cron/compose.php
php -S 0.0.0.0:8080 -t /opt/setup/runtime/cron/ui /opt/setup/runtime/cron/ui/index.php > /tmp/cron-ui.log 2>&1 &
# The UI reloads supercronic (SIGUSR2) after a crontab edit; exec keeps this PID.
echo $$ > /tmp/supercronic.pid
# No -overlapping: a job still running when its next time comes is skipped, not started twice.
exec supercronic /tmp/crontab
