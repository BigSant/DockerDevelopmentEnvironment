#!/bin/sh
# Run one cron job and record it: output, exit code, duration and trigger (scheduled or manual).
# Usage: cron-run.sh <job id> <scheduled|manual> <command>
set -u
job="$1"
trigger="$2"
command="$3"
case "$job" in *[!0-9a-f]* | '') echo "cron-run: invalid job id" >&2; exit 64 ;; esac
case "$trigger" in scheduled | manual) ;; *) echo "cron-run: invalid trigger" >&2; exit 64 ;; esac
base="${CRON_HISTORY_DIR:-/var/lib/setup-cron}"
directory="$base/runs/$job"
mkdir -p "$directory"
started=$(date +%s%3N)
log="$directory/$(date -u +%Y%m%dT%H%M%S)-$$.log"
sh -c "$command" > "$log" 2>&1
code=$?
finished=$(date +%s%3N)
printf '{"job":"%s","trigger":"%s","started":%s,"duration_ms":%s,"exit":%s,"log":"%s"}\n' \
    "$job" "$trigger" "$started" "$((finished - started))" "$code" "$(basename "$log")" >> "$base/history.jsonl"
# The container log keeps the output too.
cat "$log"
# Keep the newest runs per job.
keep="${CRON_HISTORY_KEEP:-100}"
ls -1t "$directory" | tail -n +"$((keep + 1))" | while read -r old; do rm -f "$directory/$old"; done
exit "$code"
