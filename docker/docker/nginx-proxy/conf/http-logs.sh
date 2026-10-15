#!/bin/bash
# The metrics service's view of this proxy (SETUP_ENABLE_METRICS=1), in the shared HTTP log volume: snapshots this
# container's TCP table and routes (proxy-net/), the connections clients hold to the published ports, because the
# metrics service runs in its own network namespace. The access log itself reaches the metrics service through the
# log collector; copies an older setup kept in this volume are removed.
# `http-logs.sh once` makes one pass; without an argument it repeats every SETUP_HTTP_SNAPSHOT_SECONDS.
set -uo pipefail

logs="${SETUP_HTTP_LOGS:-/var/log/setup-http}"
net="${SETUP_HTTP_PROC_NET:-/proc/net}"

pass() {
    local table
    mkdir -p "$logs/proxy-net"
    for table in tcp tcp6 route; do
        [[ -r "$net/$table" ]] || continue
        cat "$net/$table" > "$logs/proxy-net/.$table" && mv -f "$logs/proxy-net/.$table" "$logs/proxy-net/$table"
    done
}

rm -f "$logs/access.log" "$logs/access.log.1" "$logs/error.log" "$logs/error.log.1"
if [[ "${1:-}" == once ]]; then
    pass
    exit 0
fi
while true; do
    sleep "${SETUP_HTTP_SNAPSHOT_SECONDS:-10}"
    pass
done
