# Generate Doctrine migrations from an existing database

`make doctrine-diff` compares the committed table schema at `HEAD` with the running
**local** database and creates a reviewable Doctrine migration. It works without
ORM mappings, including legacy PrestaShop tables and generic PHP applications.
It does not change the project database, export/stage the canonical schema, or
run migrations on STAGE/LIVE. Use one schema tree versioned with the code.

## Opt in for one project

Run from `<project>/app`. The minimal project generator does not add these files.
Copy the optional templates from the shared setup:

```bash
mkdir -p compose database/doctrine/versions
cp ../../setup/templates/grouped/compose/doctrine.yaml compose/doctrine.yaml
cp ../../setup/templates/grouped/database/doctrine/{connection,migrations}.php database/doctrine/
```

Add to `env/common.env`, preserving other component files already listed:

```dotenv
SCHEMA_DIRECTORY=app/database/schema
PROJECT_COMPOSE_FILES=compose/doctrine.yaml
```

The schema and Doctrine directory must be in the same Git repository. If your
application is a nested repository at `app/public`, put both inside that repository
and change `SCHEMA_DIRECTORY` plus the Doctrine config bind source accordingly.
The generator discovers the config directory from the resolved service mount;
no new directory env setting is required. `migrations.php` must configure one
namespace and a migration directory inside its own directory, as the template does.

```bash
make doctrine-build
make doctrine cmd=sync-metadata-storage
make schema-export
git add database/schema database/doctrine compose/doctrine.yaml env/common.env
git commit -m "Record initial schema and migration configuration"
```

The metadata command creates Doctrine's migration history table in the local DB.
Commit the baseline **before** changing the schema. No initial snapshot is inferred
from an empty Git directory. The Doctrine tool uses PHP 8.3 independently of the
application's PHP, so the application's PHP 5.6/7.x does not need to run Doctrine.
`DOCTRINE_PHP_VERSION` optionally changes the tool's PHP; DBAL 4 requires PHP 8.2+.
The tool image includes Doctrine Migrations 3.x and DBAL 4.x.

## Capture a change

For example, a local module update adds `supplier_code` to a product table.
Once the local DB contains the intended structure:

```bash
make doctrine-diff
# Optional: select another committed baseline instead of HEAD.
# make doctrine-diff ref=v1.2.0
```

The generated `database/doctrine/versions/Version<timestamp>.php` contains
`addSql()` calls and a database-platform check. Inspect every SQL statement,
especially drops, type conversions, renames and data effects. `down()` deliberately
requires a hand-written, tested rollback; the generator cannot infer data recovery.

Because the local change already exists, **do not apply that same migration to the
local DB again**. After review, record that one version as already applied locally
(replace the example class name with the generated one):

```bash
make doctrine cmd='version "DoctrineMigrations\Version20260911203000000000" --add --no-interaction'
make schema-export
git add database/schema database/doctrine/versions
# Also stage the related application code in this same repository.
make schema-check
git commit -m "Add supplier code and its database migration"
```

A project-local lock prevents simultaneous generators from producing duplicates.
A repeated diff for exactly the same input returns the existing file. Another
uncommitted migration blocks overlapping generation: review and commit that
migration **with its updated schema**, or remove the draft you intend to replace
and regenerate the complete change. A baseline older than already-added migrations
is also rejected. This prevents silently generating the same SQL twice.

## Test and deploy

Test the migration against a disposable/restored copy of the **old** schema and
representative data, using the same MySQL/MariaDB version as deployment. The automatic
check uses empty tables and does not prove that a unique index, NOT NULL change or
type conversion will succeed with production data.

Deploy the same code, schema and migration commit to STAGE and then LIVE:

```bash
make doctrine ENV=stage cmd='migrate --dry-run'
make doctrine ENV=stage cmd='migrate --no-interaction'
# After testing and the normal production backup/deployment process:
make doctrine ENV=prod cmd='migrate --dry-run'
make doctrine ENV=prod cmd='migrate --no-interaction'
```

These commands address the chosen Compose environment on the current Docker
context; selecting `ENV=prod` is not a remote deployment connection. Supply the
correct runtime configuration and mounts on the deployment host. Existing
`ENV=test` mount isolation rules also apply: shared migration files should be
read-only and the connection must target the isolated test database.

Doctrine tracks executed versions in each database. The schema in a Git commit
represents that code version; STAGE and LIVE need not run the same commit yet.
For PrestaShop module upgrade scripts, choose one owner for each DDL change:
do not also run the same ALTER through both a module upgrade and a migration.

## How comparison is isolated and checked

1. Read the baseline from the requested immutable Git commit, ignoring staged and
   working-tree edits, then collect the local table definitions without row data.
2. Start a temporary DB using the exact running database image ID, without project
   volumes, init scripts, published ports or network access. Storage is temporary.
3. Restore old and new schemas into two empty databases. A separate Doctrine
   container can reach only that temporary DB and reads the project config through
   a read-only mount of `migrations.php` only. No connection file or real DB credentials
   are passed. The migration config must be self-contained.
4. Use DBAL to compute changes, apply the generated SQL to the temporary old schema,
   then compare its actual `SHOW CREATE TABLE` output with the target. Unsupported
   differences fail rather than producing a silently incomplete migration.
5. Remove temporary containers and write the file as the host user. Recheck the
   source DB and Git reference before saving a successful nonempty migration.

Only base tables are covered, matching `schema-export`. Views, triggers, procedures,
functions, events and row data are outside this workflow. The configured Doctrine
migration-history table is excluded from generation. Unsupported DBAL features,
external database references or differing engine configuration may require a manual
migration. The temporary DB and worker each have a 512 MiB memory limit; a very
large schema can exceed this bound and fail without changing the project DB.

Reference: [Doctrine generation with and without ORM](https://www.doctrine-project.org/projects/doctrine-migrations/en/3.9/reference/generating-migrations.html).
