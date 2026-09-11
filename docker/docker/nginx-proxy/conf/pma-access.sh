#!/bin/bash
# Render server-level rules for both PMA listeners. Nginx validates IP/CIDR values.
set -euo pipefail

case "${SETUP_ENVIRONMENT:-}" in
    local|test) exit 0 ;;
esac

emit_addresses() {
    local setting="$1" directive="$2" address
    local values="${!setting:-}"
    values="${values//,/ }"
    while IFS= read -r address; do
        [[ -z "$address" ]] && continue
        # Only literal IPs/CIDRs: never allow Nginx directives, hostnames or "all".
        if [[ ! "$address" =~ ^[0-9a-fA-F:.]+(/[0-9]{1,3})?$ ]] ||
           [[ "$address" != *.* && "$address" != *:* ]]; then
            echo "Invalid IP/CIDR in $setting" >&2
            exit 1
        fi
        printf '%s %s;\n' "$directive" "$address"
    done < <(printf '%s' "$values" | tr '[:space:]' '\n'; printf '\n')
}

emit_addresses PMA_TRUSTED_PROXIES set_real_ip_from
if [[ -n "${PMA_TRUSTED_PROXIES:-}" ]]; then
    printf 'real_ip_header X-Forwarded-For;\nreal_ip_recursive on;\n'
fi
emit_addresses PMA_ALLOWED_IPS allow
printf 'deny all;\n'
