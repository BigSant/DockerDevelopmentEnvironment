# Table schema snapshots and a pre-commit check

These commands belong to the reusable `docker/project.mk` runner. They support
MySQL/MariaDB and do not require Doctrine ORM mappings, so tables created by
PrestaShop installation/upgrade SQL are included too. The compatibility CI covers
MySQL 5.7/8.4 and MariaDB 11.4 with disposable containers. See the
[Lithuanian walkthrough](lt/07-duomenu-baze.md) for sequential examples.

## Configuration and first use

Set this in the project's public `.env` or `env/common.env`:

```dotenv
SCHEMA_DIRECTORY=database/schema
```

The path is relative to `PROJECT_DIRECTORY`. It must stay inside that project
and, for checks/hooks, belong to the repository whose commits should be
checked. If only `app/public` is your application repository, use
`SCHEMA_DIRECTORY=app/public/database/schema` instead. A hook installed in a
separate Docker repository cannot block commits in the application repository.

From the project's Docker directory, with its local DB running:

```bash
make ENV=local schema-export
# Review generated schema files, then from the application repository:
git add -A database/schema
# Back in the Docker directory:
make ENV=local schema-check
make ENV=local schema-hook-install
```

Pass `SETUP_DIRECTORY=/absolute/path/to/setup` to Make if setup is not a sibling
checkout. Adjust the `git add` path relative to the repository, not Docker.
Install once per clone/worktree hook location. The generated local hook calls
the same shared Python runner using absolute paths and the selected environment;
reinstallation is needed if checkout paths move. Shared command updates do not
require copying hook logic to each repository. Installation itself does not
connect to the DB or create a baseline, so do the export/check steps first.

Installation uses the schema repository's `hooks/pre-commit` Git path. It
preserves existing hooks and refuses `core.hooksPath` managed installations.
For Husky, pre-commit or a custom hook, add the equivalent check to the existing
manager and propagate its nonzero exit status:

```sh
make -C /absolute/project/docker SETUP_DIRECTORY=/absolute/setup ENV=local schema-check
```

The installer never changes global Git configuration. An identical installed
hook can be installed again; a differing hook requires manual integration.

## What is versioned and compared

```text
database/schema/
  README.md                     # optional handwritten documentation
  schema.manifest.json           # format version and table-to-file mapping
  table-ps_product.sql           # SHOW CREATE TABLE output for one table
  table-ps_product_lang.sql
```

Names are URL-encoded for filenames; long encoded names get a stable hash suffix
to remain within filesystem limits. The manifest is also required for an empty
database. The exporter removes the changing table-level `AUTO_INCREMENT=N`
counter but retains the column's `AUTO_INCREMENT` property, defaults, table
options, indexes, check constraints and foreign keys. It reads no table rows.
No timestamps, current DB name or credentials are added to snapshots.

Exports manage only their manifest and listed `table-*.sql` files. Dropped
tables remove the previous exported files; handwritten README files remain.
Symlinks and unrecognized files in the reserved namespace are rejected.
All DB reads complete before files are changed. Changed files are replaced
atomically individually, unchanged files keep their modification times.
Run exports sequentially; the whole directory is not an atomic transaction.

`schema-check` compares live definitions against Git **index blobs**, including
Git's alternate index during partial commits. It does not compare against the
working-tree copy or HEAD. Missing/stale files, staged files for dropped tables,
unresolved entries and symlink entries fail the check. Updating a file without
staging it still blocks a commit. Checks neither stage nor rewrite files.

DB connection errors, a mismatch between configured and running DB names,
query failures and timeouts fail the check. Credentials come from the running
`database` container's environment; errors do not print raw Compose diagnostics.
The commands only issue metadata reads and session settings, not schema changes.

## Scope and practical limits

- Snapshots cover **base tables**. Views, triggers, procedures, functions and
  scheduled events are outside this first implementation.
- The DB user must be able to see every table being tracked. MySQL only exposes
  metadata accessible to that user; the tool cannot discover hidden tables.
- Use the same MySQL version and project/module set across developers. Real
  differences in table prefixes, collation, engine or installed modules produce
  differences. There is no implicit ignore filter.
- Do not run DDL concurrently with export/check. The table list is checked
  again for additions/removals, but metadata reads do not lock the entire schema
  into a consistent snapshot across concurrent ALTER statements.
- With the hook enabled, the selected DB must be reachable for every ordinary
  commit, even a documentation-only commit. Git hooks are local and can be
  bypassed; this is not a server-side merge restriction.
- A snapshot records the expected structure. It does not prove that migrations
  or module upgrades can reproduce that structure. Migration replay in a clean
  CI DB is a separate future check, not implemented here.

See [MySQL SHOW CREATE TABLE](https://dev.mysql.com/doc/refman/8.4/en/show-create-table.html)
and [Git hooks](https://git-scm.com/docs/githooks).

## Validation

Normal tests use mocked DB responses and real temporary Git repositories:

```bash
python3 -m unittest discover -s tests -v
```

The integration test requires the existing local `mysql-local-8.4.0:latest`
image. It creates a disposable Compose DB with no published ports, no external
network and a tmpfs data directory, then removes it:

```bash
SETUP_MYSQL_SCHEMA_INTEGRATION=1 python3 -m unittest discover -s tests -p 'test_database_schema_integration.py' -v
```

It tests a real hook/commit, schema drift, export without staging, a successful
commit after staging, ignored row/AUTO_INCREMENT changes, and an offline DB.
It does not start, stop or modify any existing project database.
