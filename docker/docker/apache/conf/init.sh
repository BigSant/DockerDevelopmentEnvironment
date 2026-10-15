#!/bin/bash
set -euo pipefail

domain="${1:-}"
document_root="${2:-}"
env="${3:-}"
sites_conf="/usr/local/apache2/conf/sites.conf"
export HTTP_FORCE_HTTPS="${HTTP_FORCE_HTTPS:-off}"
case "$HTTP_FORCE_HTTPS" in on|off) ;; *) echo 'HTTP_FORCE_HTTPS must be on or off' >&2; exit 1 ;; esac

# Apache serves plain HTTP only: nginx-proxy terminates TLS and owns the certificates.

# ── Validate required args ────────────────────────────────────────────────────
if [[ -z "$domain" ]]; then
  echo "[init] ERROR: domain argument is required" >&2
  exit 1
fi

# ── Effective project drop-ins: copy and render *.template ──────────────────────
sh /usr/local/bin/render-templates.sh \
  /usr/local/apache2/conf/project /usr/local/apache2/conf/project.r \
  /usr/local/apache2/conf/project.env /usr/local/apache2/conf/project.r.env

# ── Substitute domain placeholder ─────────────────────────────────────────────
sed -i "s/DOMAIN_VAR/${domain}/g" "$sites_conf"
echo "[init] Domain set to: ${domain}"

# ── Substitute document root (optional) ───────────────────────────────────────
if [[ -n "$document_root" && "$document_root" != "/" ]]; then
  # Strip any leading/trailing slashes for a clean sub-path
  document_root="${document_root#/}"
  document_root="${document_root%/}"
  sed -i "s#/var/www/html#/var/www/html/${document_root}#g" "$sites_conf"
  echo "[init] Document root set to: /var/www/html/${document_root}"
fi

# ── Export ENV_PROFILE for Apache <If> directives ────────────────────────────
if [[ -n "$env" ]]; then
  echo "SetEnv ENV_PROFILE ${env}" >> /usr/local/apache2/conf/httpd.conf
  export ENV_PROFILE="${env}"
  echo "[init] ENV_PROFILE set to: ${env}"
fi

# ── Start Apache ──────────────────────────────────────────────────────────────
echo "[init] Starting Apache httpd..."
exec httpd-foreground
