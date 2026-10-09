#!/bin/bash
# Render the client IP rules for every web entry point and the trusted proxy list.
#
# ALLOWED_IPS applies to all entry points; <NAME>_ALLOWED_IPS replaces it for one
# (SITE, PMA, MAILPIT, CRON, DBMONITOR, METRICS). A non-empty list allows only those IPs/CIDRs.
# An empty list leaves local/test and the site open; on stage/prod the tools are
# denied (the runner already refuses to start them without a list).
set -euo pipefail

output="${SETUP_ACCESS_DIR:-/etc/nginx/setup-access}"
real_ip="${SETUP_REAL_IP_FILE:-/etc/nginx/setup-real-ip.conf}"
mkdir -p "$output"

addresses() {
    local setting="$1" values address
    values="${!setting:-}"
    values="${values//,/ }"
    for address in $values; do
        # Only literal IPs/CIDRs: never Nginx directives, hostnames or "all".
        if [[ ! "$address" =~ ^[0-9a-fA-F:.]+(/[0-9]{1,3})?$ ]] ||
           [[ "$address" != *.* && "$address" != *:* ]]; then
            echo "Invalid IP/CIDR in $setting: $address" >&2
            exit 1
        fi
        printf '%s\n' "$address"
    done
}

for name in site pma mailpit cron dbmonitor metrics; do
    setting="$(printf '%s' "$name" | tr '[:lower:]' '[:upper:]')_ALLOWED_IPS"
    [[ -n "${!setting:-}" ]] || setting=ALLOWED_IPS
    list="$(addresses "$setting")"
    {
        if [[ -n "$list" ]]; then
            printf 'allow %s;\n' $list
            printf 'deny all;\n'
        elif [[ "$name" != site && "${SETUP_ENVIRONMENT:-local}" != local && "${SETUP_ENVIRONMENT:-local}" != test ]]; then
            printf 'deny all;\n'
        fi
    } > "$output/$name.conf"
done

# Requests reach this proxy only through the host (ports bound to 127.0.0.1) or the
# Docker network, so the forwarded client address from those peers is trusted.
TRUSTED_PROXIES="${TRUSTED_PROXIES:-127.0.0.1 ::1 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16}"
trusted="$(addresses TRUSTED_PROXIES)"
{
    printf 'set_real_ip_from %s;\n' $trusted
    printf 'real_ip_header X-Forwarded-For;\nreal_ip_recursive on;\n'
} > "$real_ip"
