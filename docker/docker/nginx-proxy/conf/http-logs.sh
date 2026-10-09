#!/bin/bash
# The metrics service's view of this proxy (SETUP_ENABLE_METRICS=1), in the shared HTTP log volume:
# - keeps the site's access log copy bounded: over METRICS_HTTP_LOG_MB it becomes access.log.1 and nginx
#   reopens access.log, so the volume holds at most two files (docker logs keep their own copy);
# - snapshots this container's TCP table and routes (proxy-net/), the connections clients hold to the
#   published ports, because the metrics service runs in its own network namespace.
# `http-logs.sh once` makes one pass; without an argument it repeats every SETUP_HTTP_SNAPSHOT_SECONDS.
set -uo pipefail

logs="${SETUP_HTTP_LOGS:-/var/log/setup-http}"
net="${SETUP_HTTP_PROC_NET:-/proc/net}"
limit=$(( ${METRICS_HTTP_LOG_MB:-32} * 1024 * 1024 ))

pass() {
    local size table
    size="$(stat -c %s "$logs/access.log" 2>/dev/null || echo 0)"
    if (( size > limit )); then
        mv -f "$logs/access.log" "$logs/access.log.1" && nginx -s reopen
    elif [[ -e "$logs/access.log.1" && ! -e "$logs/access.log" ]]; then
        # Rotated but not reopened yet (nginx was starting): ask again.
        nginx -s reopen
    fi
    mkdir -p "$logs/proxy-net"
    for table in tcp tcp6 route; do
        [[ -r "$net/$table" ]] || continue
        cat "$net/$table" > "$logs/proxy-net/.$table" && mv -f "$logs/proxy-net/.$table" "$logs/proxy-net/$table"
    done
}

if [[ "${1:-}" == once ]]; then
    pass
    exit 0
fi
while true; do
    sleep "${SETUP_HTTP_SNAPSHOT_SECONDS:-10}"
    pass
done
