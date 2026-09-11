# phpMyAdmin access by environment

PMA is optional. A new project still contains only the four core services. Add
the following to `compose/common.yaml` when PMA is needed in every environment:

```yaml
include:
  - ${ROOT_DIRECTORY}/docker/pma/docker-compose.yml

services:
  nginx-proxy:
    environment:
      PMA_ALLOWED_IPS: ${PMA_ALLOWED_IPS:-}
      PMA_TRUSTED_PROXIES: ${PMA_TRUSTED_PROXIES:-}
```

Set the shared client list in `env/common.env`, replacing these documentation
addresses with your office/VPN/public client IPs:

```dotenv
PMA_ALLOWED_IPS="203.0.113.10 198.51.100.0/24 2001:db8::10"
```

Set `PMA_TRUSTED_PROXIES` in the env file only if an upstream proxy is used.
Missing values default to empty. Nothing is added to newly generated projects.

PMA and Mailpit have no Compose profiles. Including a service's YAML makes it
part of the active environment; no profile selection or reset is needed.
If an existing project uses the full shared `docker/docker-compose.yml`, that
file already includes both services, so both are active. Use explicit component
includes as shown here to select them by environment; do not include a service twice.

The runner loads `base.yaml`, `common.yaml`, explicitly selected extra files,
then `<ENV>.yaml`. For Mailpit only on local machines, add to `compose/local.yaml`:

```yaml
include:
  - ${ROOT_DIRECTORY}/docker/mailpit/docker-compose.yml
```

| Setting | Meaning |
| --- | --- |
| `PMA_ALLOWED_IPS` | Client IPs/CIDRs allowed on stage/prod. Empty or absent denies everyone. Separate entries with spaces, commas or newlines. Supports IPv4 and IPv6. |
| `PMA_TRUSTED_PROXIES` | Optional addresses/CIDRs of your upstream proxies. Empty or absent means forwarded headers cannot change the client IP used for access checks. |
| `SETUP_ENVIRONMENT` | Supplied automatically by shared Compose from `ENV`. Keep it unchanged: local/test are unrestricted; stage/prod are restricted. Image build target and cache mode do not determine PMA access. |

The YAML forwards dotenv values into **`nginx-proxy`**, not the PMA service.
Use `env/common.env` for shared values. If the lists should differ, set the same
keys in `env/stage.env` or `env/prod.env`: environment-specific values replace
the common values. An explicit `PMA_ALLOWED_IPS=` clears an inherited allowlist;
omitting the key inherits it. This grouped layout does not read a root `.env`.
LIVE uses this setup's `ENV=prod`, `env/prod.env` and `compose/prod.yaml`.

The rules protect both HTTP and HTTPS for `pma.<DOMAIN>` and
`www.pma.<DOMAIN>`. Other application hosts are unaffected. Non-matching clients
receive HTTP 403. A restricted environment with no list remains closed. Invalid
IP/CIDR values stop proxy startup, rather than silently making PMA accessible.
Local/test ignore the shared list so development access stays unchanged.

## If another Nginx or load balancer is in front

For example, the public proxy is `10.20.0.5` and your office public IP is
`203.0.113.10`. Set `PMA_TRUSTED_PROXIES=10.20.0.5` and
`PMA_ALLOWED_IPS=203.0.113.10` in the selected env file. The proxy must send the actual client address
in `X-Forwarded-For`, replacing it or appending the verified connecting address.
Use the proxy source IP **as seen by the container**; Docker/NAT can change it.
Use trusted addresses only, never `0.0.0.0/0`, `::/0` or a network shared with
untrusted clients. Do not put the proxy IP in the client allowlist merely to
make a 403 disappear: that would allow every user behind it.

Without this explicit trust, Nginx checks the connecting IP and ignores a
client-supplied `X-Forwarded-For`. With it, recursive real-IP processing chooses
the last non-trusted address in the chain. See the official
[Nginx real-IP module](https://nginx.org/en/docs/http/ngx_http_realip_module.html)
and [access module](https://nginx.org/en/docs/http/ngx_http_access_module.html).

Keep PMA behind `nginx-proxy`: do not publish PMA's own port on stage/prod or
route an external proxy directly to it, because that bypasses these rules.
The shared PMA service does not publish a host port. It receives database login
credentials at runtime, so the IP rule controls access to that database UI.

## Apply and verify

After updating shared setup sources, build the new images explicitly in the
project directory, then start using the usual deployment procedure:

```bash
make check ENV=stage
make build ENV=stage
make up ENV=stage
```

Use `ENV=prod` for LIVE. Stage/prod env files, code, database and certificates
must already be prepared for that environment. Local usage is `make check`,
`make build`, `make up`; its PMA URL is `http://pma.<DOMAIN>/`.
Later changes only to the IP list in an env file need `make up ENV=stage` to recreate
the proxy with the changed container environment; no image rebuild is needed.

If PMA is served publicly over HTTPS, set its canonical URI in the corresponding
environment YAML, alongside any existing service settings:

```yaml
services:
  pma:
    environment:
      PMA_ABSOLUTE_URI: https://pma.${DOMAIN}/
```

Open PMA once from an allowed network and once from another network, testing
HTTP and HTTPS. The second must return 403. If every request is denied, check
the source IP shown in Nginx logs and the trusted proxy setting. Do not broaden
the allowlist as a substitute for verifying the client IP.

Automated integration tests use isolated disposable containers, exercising
HTTP/HTTPS, both hostnames, IPv4/CIDR/IPv6, forged forwarded headers, an explicit
trusted proxy, unchanged application access and invalid configuration rejection.
