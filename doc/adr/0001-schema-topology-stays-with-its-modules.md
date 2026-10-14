# 1. Schema topology stays with its modules

Status: accepted (2026-10-14)

## Context

`DATABASE_EXTRA_SCHEMAS` touches most database modules: the snapshot keeps each schema in its own
directory, migrations and after-import SQL have per-schema folders, backups and hooks switch schemas with
`USE`, and the table registry, manifest and backups name an extra schema's table `schema.table`. An
architecture review (2026-10-14) proposed one SchemaTopology module owning all of it: the schema list,
the main schema, name splitting, every per-schema directory and `USE`.

A closer look showed:

- The `schema.table` convention already had one parser (`split_name`, ~20 call sites, most of them pure
  functions without settings). Only the registry and backup split names their own way, and only the
  registry disagreed (for table names that contain a dot).
- Each per-schema directory is defined by its own module's directory and rules: the snapshot's
  (`main_directory`), the migrations' (`migration_folder`, `folder_schema`), after-import's (`hook_schema`).
- `USE` is one line in each of three places.

## Decision

No SchemaTopology module. `split_name` and `quoted` live in `database_client` beside `SCHEMA_NAME` and
`extra_schemas`, and every reader of `schema.table` names uses them. The per-schema directories and the
`USE` lines stay in the modules whose directories and statements they belong to.

## Consequences

- Moving the directory helpers into one module would relocate them, not concentrate them: each would
  still need its module's directory and rules (the deletion test fails). Do not re-propose it unless a
  per-schema rule starts being duplicated across modules.
- A change to the naming convention is made in `database_client` only.
- A change to one kind of per-schema directory is still made in that module, which is where its readers look.
