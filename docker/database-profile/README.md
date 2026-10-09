# Database profile defaults

Per-profile database conventions that are not baked into any image (so changing them never
rebuilds one):

- `table-registry.txt` — default table classification (group, base data-set rule, LIVE import
  rule); a project's `database/table-registry.txt` is read first and wins.
- `after-import/*.sql` — safe defaults applied after every DB load outside prod, before the
  project's own after-import scripts. `${DOMAIN}`, `${DB_PREFIX}` and `${PS_SSL}` are substituted;
  a generic project (PROFILE other than prestashop) gets only `${DOMAIN}` and no `{p}` prefix.
