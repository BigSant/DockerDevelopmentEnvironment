# Project fixture data

Put numbered SQL files in `common/`, `local/` and `test/`. These directories are
initially empty; add synthetic records appropriate to this application's schema.

```sh
make fixtures-plan set=local
make fixtures-load set=local
make fixtures-load set=test ENV=local
make db-import file=/path/to/dump.sql fixtures=local
```

Common files run first, then the selected set, in filename order within each
group. Only direct `*.sql` files are loaded. New sets can be added as directories.
Fixtures are explicit; normal startup and dump imports do not load them.

`set=test` selects data, not a separate database. `ENV` selects the Docker stack.
There is no automatic reset or execution history: write repeatable SQL using
stable keys, upserts or scoped cleanup, and order files for foreign keys. Each file
uses a separate DB session. A failure stops later files but does not undo earlier
statements. `${DOMAIN}` may be used like in the project's after-import hooks.
