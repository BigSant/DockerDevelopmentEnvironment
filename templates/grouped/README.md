# Project environment

Shared commands and images come from the sibling setup checkout. Project sources
are grouped under env/, compose/, config/, qa/ and database/. The application
checkout stays at <project>/app/public; persistent data and reports stay at
<project>/data. Sources can live in <project>/docker or directly in <project>/app.

1. Run make init to create a private env/local.env template and writable directories.
2. Edit env/local.env: set credentials, domain and two unused host ports. Supply
   the application checkout and TLS certificates (host provisioning is separate).
3. Run make doctor. Build project images with make build and obtain external
   images with make pull as needed, then make up.
4. Use make ps, make logs, make shell, make phpstan, make phpcs or make e2e.

Use ENV=prod with a separately configured env/prod.env. This scaffold is not a
production deployment policy. Its production overlay isolates data paths; review
DB permissions, durability, secrets and application image packaging separately.

Redis is optional: PROFILES=mailpit,pma,cron,redis. Real private env files and
.generated/ never enter Git. make config writes a private snapshot for inspection.
Never edit the snapshot as source. All source overrides belong under compose/.

QA and schema files live in this configuration repository by default. To couple
them to application commits, put them in the application repository and override
mounts/SCHEMA_DIRECTORY accordingly. Hooks are never enabled by preparation.

After-import hooks live in database/sql/after-import/{common,local,prod}/.
Fixture data lives separately in database/fixtures/{common,local,test}/.
Use `make fixtures-plan set=local` and `make fixtures-load set=local`, or append
`fixtures=local` to `make db-import file=...`. The set selects data independently
of Docker `ENV`; it does not create an isolated test database.
make db-import-plan file=/path/dump.sql previews; make db-import imports explicitly.
Hook SQL may use the validated ${DOMAIN} placeholder. Schema snapshots do not
prove migration replay; a disposable CI database should check that separately.

See setup/doc/PROJECT_TEMPLATES.md and setup/doc/ENVIRONMENT_COMMANDS.md.
