#!/bin/bash
# Render the client IP rules for every web entry point, the tools' passwords and the trusted proxy list.
#
# ALLOWED_IPS applies to all entry points; <NAME>_ALLOWED_IPS replaces it for one
# (SITE, PMA, MAILPIT, CRON, DBMONITOR, BACKUP, METRICS, and every PROXY_TOOLS name). A non-empty list
# allows only those IPs/CIDRs. An empty list leaves local/test and the site open; on stage/prod the tools
# are denied (the runner already refuses to start setup's tools without a list).
# <NAME>_HTPASSWD (htpasswd lines user:hash, separated by whitespace) adds Basic-auth on top of the IP rule
# for PMA, MAILPIT, CRON, BACKUP, METRICS and the PROXY_TOOLS names; empty keeps the IP rule alone. Not for the
# site (the application guards itself) nor DBMONITOR, whose bearer token uses the same header; METRICS turns it
# off again for /metrics, which has such a token (sites.metrics.conf).
# PROXY_TOOLS: a project's own nginx-proxy sites (config/nginx-proxy/), which include
# /etc/nginx/setup-access/<name>.conf like setup's tools do.
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

# Writes $output/<name>.htpasswd from <NAME>_HTPASSWD and prints its path; prints nothing when it is empty.
# The entries are secrets: an error names the setting and the entry's position, never its text.
users() {
    local name="$1" setting entries entry position=0 file
    setting="$(printf '%s' "$name" | tr '[:lower:]' '[:upper:]')_HTPASSWD"
    set -f
    # shellcheck disable=SC2206 # split on any whitespace, newlines included, without globbing
    entries=(${!setting:-})
    set +f
    [[ ${#entries[@]} -gt 0 ]] || return 0
    for entry in "${entries[@]}"; do
        position=$((position + 1))
        if [[ ! "$entry" =~ ^[^:]+:.+$ ]]; then
            echo "Invalid entry $position in $setting: expected user:hash (htpasswd -nbB USER PASSWORD)" >&2
            exit 1
        fi
    done
    file="$output/$name.htpasswd"
    printf '%s\n' "${entries[@]}" > "$file.tmp"
    chmod 0644 "$file.tmp"
    mv -f "$file.tmp" "$file"
    printf '%s' "$file"
}

setup_names="site pma mailpit cron dbmonitor backup metrics"
project_names="${PROXY_TOOLS:-}"
project_names="${project_names//,/ }"
for name in $project_names; do
    if [[ ! "$name" =~ ^[a-z][a-z0-9_]*$ || " $setup_names " == *" $name "* ]]; then
        echo "Invalid PROXY_TOOLS name: $name (lowercase letters, digits and _, not one of setup's: $setup_names)" >&2
        exit 1
    fi
done
password_names=" pma mailpit cron backup metrics $project_names "

for name in $setup_names $project_names; do
    setting="$(printf '%s' "$name" | tr '[:lower:]' '[:upper:]')_ALLOWED_IPS"
    [[ -n "${!setting:-}" ]] || setting=ALLOWED_IPS
    list="$(addresses "$setting")"
    file=""
    [[ "$password_names" != *" $name "* ]] || file="$(users "$name")"
    {
        if [[ -n "$list" ]]; then
            printf 'allow %s;\n' $list
            printf 'deny all;\n'
        elif [[ "$name" != site && "${SETUP_ENVIRONMENT:-local}" != local && "${SETUP_ENVIRONMENT:-local}" != test ]]; then
            printf 'deny all;\n'
        fi
        if [[ -n "$file" ]]; then
            printf 'auth_basic "%s";\nauth_basic_user_file %s;\n' "$name" "$file"
        fi
    } > "$output/$name.conf"
done

# Who may tell this proxy the client address (X-Forwarded-For) and scheme (X-Forwarded-Proto):
# - TRUSTED_PROXIES, when set, in either mode;
# - ports on 127.0.0.1 (default): the host proxy (DirectAdmin / local Nginx), which reaches the
#   container through Docker's port proxy as the network gateway, plus loopback. Not the whole
#   private ranges: other containers and networks must not forward a client address;
# - ports on another address (SETUP_DIRECT_CLIENTS=1, e.g. a VPN): nobody. Clients connect
#   directly and a forwarded header is theirs to invent.
gateways() {
    local interface destination gateway flags rest
    while read -r interface destination gateway flags rest; do
        # Default route with RTF_GATEWAY; /proc/net/route stores the IPv4 address little-endian.
        if [[ "$destination" == 00000000 && "$gateway" =~ ^[0-9A-Fa-f]{8}$ && "$gateway" != 00000000 ]] &&
           (( 0x$flags & 2 )); then
            printf '%d.%d.%d.%d\n' "0x${gateway:6:2}" "0x${gateway:4:2}" "0x${gateway:2:2}" "0x${gateway:0:2}"
        fi
    done < <(tail -n +2 "${SETUP_ROUTE_FILE:-/proc/net/route}" 2>/dev/null)
}
if [[ -n "${TRUSTED_PROXIES:-}" ]]; then
    trusted="$(addresses TRUSTED_PROXIES)"
elif [[ "${SETUP_DIRECT_CLIENTS:-0}" == 1 ]]; then
    trusted=""
else
    TRUSTED_PROXIES="127.0.0.1 ::1 $(gateways | sort -u | tr '\n' ' ')"
    trusted="$(addresses TRUSTED_PROXIES)"
fi
{
    if [[ -n "$trusted" ]]; then
        printf 'set_real_ip_from %s;\n' $trusted
        printf 'real_ip_header X-Forwarded-For;\nreal_ip_recursive on;\n'
    fi
    # The peer that connected, before X-Forwarded-For: only a trusted one may set the scheme.
    printf 'geo $realip_remote_addr $setup_trusted_proxy {\n    default 0;\n'
    [[ -z "$trusted" ]] || printf '    %s 1;\n' $trusted
    printf '}\n'
} > "$real_ip"
