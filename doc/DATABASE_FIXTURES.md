# SQL fixture sets

Fixtures are versioned, project-owned data files, separate from exported schema
definitions and from the SQL fixes applied after a dump import. Shared setup
provides the loader; each project decides which rows its fixtures contain.

Configure a path relative to the project root in `env/common.env`:

```dotenv
FIXTURES_DIRECTORY=app/database/fixtures
```

For consolidated sources, this produces:

```text
app/database/
  schema/                    # existing table definitions
  fixtures/
    common/                  # SQL shared by all fixture sets
    local/                   # local development data
    test/                    # deterministic test data
```

Use numbered `.sql` files, e.g. `010-customers.sql`, `020-products.sql`.
The loader executes direct `common/*.sql` files in filename order, then direct
`<selected-set>/*.sql` files in filename order. Numbering is per group: every
common file runs before any selected file. Subdirectories are not scanned.
Additional sets can be introduced by creating a directory such as `demo/`.
`set=common` runs the common group once. Missing sets fail; an existing empty set
is allowed. If both groups have no SQL files, loading is a no-op.

```sh
make fixtures-plan set=local
make fixtures-load set=local
make fixtures-load set=test ENV=local

# Optional fixtures after dump + normal after-import SQL:
make db-import-plan file=/path/to/dump.sql fixtures=local
make db-import file=/path/to/dump.sql fixtures=local
```

`set` chooses data; `ENV` chooses the existing Docker stack/database. `set=test`
does not create a test database, change DB credentials, or isolate data from the
local shop. Use a separately configured project/stack for isolated tests. Loading
one set does not remove rows previously loaded from another set. Fixture SQL
must implement any intended replacement or cleanup explicitly.

No fixtures run automatically on `make up`, image build, schema export or a
plain `db-import`. The combined import selects dump → common after-import hooks
→ environment after-import hooks → common fixtures → selected fixtures. All
inputs are preflighted before the dump starts, and the entire sequence uses one
project/environment import lock. Standalone fixture loading takes that same lock.

There is no execution history: each invocation runs every selected SQL file.
Author repeatable SQL using stable keys and appropriate upserts or scoped
cleanup, and respect foreign-key order. Do not assume an empty database or an
automatic reset. Files run in separate MySQL sessions; session variables and
temporary tables do not carry into the next file. A failure stops later files,
but previously executed statements are not automatically rolled back. There is
no SQL statement filter: the loader executes the selected project's SQL as written.

The loader reuses the running database container's credentials, checks its DB
name against project settings, and does not start containers. `${DOMAIN}` is
the only supported fixture substitution, with the same hostname validation as
after-import hooks; there is no arbitrary shell/env expansion. Source files
are retained unchanged. Rendered SQL uses private temporary files under
`.generated/`. Group/file symlinks are rejected to prevent unintended set selection.

New grouped project templates contain empty common/local/test directories. They
do not invent application records. Add synthetic data appropriate to the actual
application schema. Fixtures live in the environment repository alongside schema
exports; an independent application checkout can have its own test fixtures too.

Validation includes an opt-in Make/Compose/MySQL integration test using an isolated
container with a tmpfs DB and no network/host DB mount:

```sh
SETUP_MYSQL_FIXTURES_INTEGRATION=1 python3 -m unittest discover -s tests -p test_database_fixtures_integration.py -v
```

It uses the already built `mysql-local-8.4.0:latest` image and removes its container
afterward. It checks selection, repeatable loading, import ordering, preview-only
behavior and failure handling without accessing an existing project's database.
