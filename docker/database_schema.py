"""Read MySQL table DDL and compare it with the exact Git index being committed.

The snapshot covers the main database and every DATABASE_EXTRA_SCHEMAS schema. With one database its tables
are table-<name>.sql; with extra schemas every schema has its own directory: <DATABASE_NAME>/table-<name>.sql
and <schema>/table-<name>.sql. Only the export chooses that layout; everything that reads a snapshot follows
the manifest, which names an extra schema's table schema.table like the registry."""

import copy
import functools
import json
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import quote


MANIFEST = "schema.manifest.json"
from database_client import SCHEMA_NAME, quoted, split_name
# setup_migrations records applied migrations, setup_translations written translations; they belong to setup, not to
# the project schema.
TABLES_SQL = ("SELECT HEX(TABLE_NAME) FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE' "
              "AND TABLE_NAME NOT IN ('setup_migrations', 'setup_translations') ORDER BY BINARY TABLE_NAME;\n")


def main_directory(project):
    """The main database's snapshot directory: None (flat) with one database, DATABASE_NAME beside extra schemas."""
    from database_client import extra_schemas
    if not extra_schemas(project.settings):
        return None
    name = project.settings["DATABASE_NAME"]
    if not SCHEMA_NAME.fullmatch(name or ""):
        raise ValueError("With DATABASE_EXTRA_SCHEMAS, DATABASE_NAME names the snapshot directory: use 1-64 letters, digits or _")
    return name


def table_file(name, main=None):
    """Snapshot file of a table: an extra schema's in its directory, the main database's in main (or flat)."""
    schema, table = split_name(name)
    folder = schema or main
    return (folder + "/" if folder else "") + _table_file(table)


def valid_table_file(name, file):
    """A manifest's file for a table, in either layout: the main database's flat or in one directory."""
    if split_name(name)[0]:
        return file == table_file(name)
    folder, _, base = file.rpartition("/")
    return base == _table_file(name) and (not folder or bool(SCHEMA_NAME.fullmatch(folder)))


def _table_file(name):
    encoded = quote(name, safe="")
    if len(encoded) > 200:
        encoded = encoded[:150] + "-" + hashlib.sha256(name.encode()).hexdigest()[:24]
    return "table-" + encoded + ".sql"


# MySQL spells the same column collation differently for a table recreated from a snapshot than
# for the original (CHARACTER SET x COLLATE x_ci vs COLLATE x_ci, or a restated default).
# utf8mb4's default differs between MySQL and MariaDB, so it is never treated as implied.
DEFAULT_COLLATIONS = {"utf8mb3": "utf8mb3_general_ci", "utf8": "utf8_general_ci", "latin1": "latin1_swedish_ci",
                      "ascii": "ascii_general_ci"}


def canonical_collation(match):
    """One spelling per column collation: the default as CHARACTER SET alone, others as COLLATE alone."""
    charset, collation = match[1], match[2]
    if DEFAULT_COLLATIONS.get(charset) == collation:
        return f"CHARACTER SET {charset}"
    return f"COLLATE {collation}" if collation.startswith(charset + "_") else match[0]


def normalize_ddl(ddl):
    # Only the table option after ENGINE; retain column AUTO_INCREMENT and comments/defaults.
    ddl = re.sub(r"(?m)^(\) ENGINE=\S+) AUTO_INCREMENT=\d+\b", r"\1", ddl)
    ddl = re.sub(r"CHARACTER SET (\w+) COLLATE (\w+)", canonical_collation, ddl).rstrip()
    # Idempotent: a snapshot file already ends in ";" (committed snapshots are normalised again).
    return (ddl[:-1] if ddl.endswith(";") else ddl) + ";\n"


def database_tables(project):
    return [bytes.fromhex(name).decode("utf-8") for name, in project.database.query(TABLES_SQL)]


def extra_tables(project):
    """Tables of DATABASE_EXTRA_SCHEMAS as schema.table, the table registry's names for them."""
    from database_client import extra_schemas
    schemas = extra_schemas(project.settings)
    if not schemas:
        return []
    listed = ','.join(f"'{schema}'" for schema in schemas)  # validated names: letters, digits, _
    rows = project.database.query("SELECT HEX(TABLE_SCHEMA), HEX(TABLE_NAME) FROM information_schema.TABLES "
                                  f"WHERE TABLE_SCHEMA IN ({listed}) AND TABLE_TYPE = 'BASE TABLE' ORDER BY 1, 2;\n")
    return [bytes.fromhex(schema).decode("utf-8") + "." + bytes.fromhex(table).decode("utf-8") for schema, table in rows]


def registry_tables(project):
    """Every table the table registry must classify: the main database's, then the extra schemas'."""
    return database_tables(project) + extra_tables(project)


def read_schema(project, require_classified=False):
    """Snapshot files of every base table, the extra schemas' included, except those the registry marks for a
    guarded DROP."""
    tables = registry_tables(project)
    exclude = registry_exclusions(project, tables, require_classified)
    names = [name for name in tables if name not in exclude]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate table names in schema response")
    definitions = {}
    if names:
        statements = "SET SESSION sql_quote_show_create=1; SET SESSION sql_mode='';\n"
        statements += "\n".join("SHOW CREATE TABLE " + quoted(name) + ";" for name in names)
        # One row per statement, in order: an extra schema's row names the table without its schema.
        rows = project.database.query(statements)
        if len(rows) != len(names):
            raise ValueError("Schema changed during collection; retry without concurrent DDL")
        for name, fields in zip(names, rows):
            if len(fields) != 2:
                raise ValueError("Unexpected SHOW CREATE TABLE response")
            table, ddl = fields
            if table != split_name(name)[1] or name in definitions or not ddl.startswith("CREATE TABLE "):
                raise ValueError("Incomplete or unexpected table definition")
            definitions[name] = normalize_ddl(ddl)
    if set(definitions) != set(names):
        raise ValueError("Schema changed during collection; retry without concurrent DDL")
    # Catch table additions/removals during collection; this does not lock out concurrent ALTER.
    after = [name for name in registry_tables(project) if name not in exclude]
    if after != names:
        raise ValueError("Table list changed during collection; retry without concurrent DDL")
    main = main_directory(project)
    files = {table_file(name, main): definitions[name].encode() for name in sorted(names)}
    manifest = {"format": 1, "dialect": "mysql", "tables": [
        {"name": name, "file": table_file(name, main)} for name in sorted(names)]}
    files[MANIFEST] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    return files


def schema_directory(project):
    committing = getattr(project, "committing_schema_directory", None)
    if committing is not None:
        return committing  # Set by in_committing_checkout for a linked worktree's commit.
    configured = project.settings.get("SCHEMA_DIRECTORY", "")
    if not configured:
        raise ValueError("Database as code is off (DATABASE_AS_CODE=off); remove that line to use the schema, "
                         "registry and migrations")
    directory = (project.root / configured).resolve()
    if directory == project.root or not directory.is_relative_to(project.root):
        raise ValueError("SCHEMA_DIRECTORY must be a subdirectory inside the project")
    return directory


def managed_file(name):
    """The manifest, a main table's file, or an extra schema's (<schema>/table-*.sql)."""
    schema, _, base = name.rpartition("/")
    if schema and not SCHEMA_NAME.fullmatch(schema):
        return False
    return (base == MANIFEST and not schema) or (base.startswith("table-") and base.endswith(".sql"))


def snapshot_files(directory):
    """{relative name: path} of the managed files in a schema directory and its schema subdirectories."""
    files = {}
    for path in directory.iterdir() if directory.is_dir() else ():
        if path.is_dir() and not path.is_symlink() and SCHEMA_NAME.fullmatch(path.name):
            files.update({f"{path.name}/{child.name}": child for child in path.iterdir() if managed_file(f"{path.name}/{child.name}")})
        elif managed_file(path.name):
            files[path.name] = path
    return files


def registry_exclusions(project, tables, require_classified=False):
    """Tables the registry marks for DROP; optionally fail on tables it does not know."""
    from database_registry import dropped, load, unknown_tables
    entries = load(project)
    if not entries:
        return set()
    unknown = unknown_tables(entries, tables)
    if unknown and require_classified:
        raise ValueError(f"{len(unknown)} tables are not in the table registry: {', '.join(unknown[:8])}"
                         f"{' …' if len(unknown) > 8 else ''}\nRun make schema-resolve to include them or mark them for a DROP.")
    if unknown:
        print(f"Warning: {len(unknown)} tables are not classified yet (make schema-resolve).")
    return set(dropped(entries, tables))


def export_schema(project, files=None):
    directory = schema_directory(project)
    if files is None:
        files = read_schema(project)  # Finish all DB reads before changing any files.
    directory.mkdir(parents=True, exist_ok=True)
    previous, previous_tables = set(), set()
    manifest_path = directory / MANIFEST
    if manifest_path.is_symlink():
        raise ValueError("Schema manifest must not be a symlink")
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if (not isinstance(manifest, dict) or manifest.get("format") != 1
                or manifest.get("dialect") != "mysql" or not isinstance(manifest.get("tables"), list)):
            raise ValueError("Unsupported schema manifest; existing files were preserved")
        for table in manifest["tables"]:
            if (not isinstance(table, dict) or not isinstance(table.get("name"), str)
                    or not valid_table_file(table["name"], table.get("file"))):
                raise ValueError("Invalid table filename in schema manifest")
            previous.add(table["file"])
            previous_tables.add(table["name"])
    for schema in {name.split("/")[0] for name in files.keys() | previous if "/" in name}:
        if (directory / schema).is_symlink() or ((directory / schema).exists() and not (directory / schema).is_dir()):
            raise ValueError(f"Schema directory of {schema} must be a plain directory")
    for name, path in snapshot_files(directory).items():
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Refusing to replace non-regular schema file: {name}")
        if name != MANIFEST and name not in previous:
            raise ValueError(f"Unmanaged schema file: {name}; review it before export")
    # Replace each file atomically; unchanged exports preserve mtimes.
    for name, contents in files.items():
        target = directory / name
        if target.exists() and target.read_bytes() == contents:
            continue
        target.parent.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(contents)
        try:
            temporary.chmod(0o644)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    for name in previous - files.keys():
        (directory / name).unlink(missing_ok=True)
        if "/" in name and not any((directory / name).parent.iterdir()):
            (directory / name).parent.rmdir()  # The schema has no tables left in the snapshot.
    # A table gone from the snapshot takes its own registry line with it (wildcards and the profile stay).
    gone = previous_tables - {table["name"] for table in json.loads(files[MANIFEST])["tables"]}
    if gone and getattr(project, "settings", None) is not None:
        from database_registry import remove_entries
        removed = remove_entries(project, gone)
        if removed:
            print(f"Removed the table registry lines of {len(removed)} dropped tables: {', '.join(sorted(removed)[:5])}"
                  f"{' …' if len(removed) > 5 else ''}")
    print(f"Exported {len(files) - 1} tables to {directory}. Review, then git add -A that directory.")


HOOK_GIT_VARIABLES = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")


def git_env(clean=False):
    # Hooks may export relative index/Git paths; preserve their meaning across git -C.
    env = os.environ.copy()
    for key in HOOK_GIT_VARIABLES:
        if clean:
            env.pop(key, None)  # Let git -C find the repository of the directory it is given.
        elif env.get(key):
            env[key] = str(Path(env[key]).absolute())
    if not clean and env.get("GIT_DIR") and not env.get("GIT_WORK_TREE"):
        # A linked worktree's hook gets GIT_DIR alone, and git takes the hook's directory as the work
        # tree; git -C into the schema directory would make that subdirectory the work tree.
        top = hook_work_tree(env["GIT_DIR"], os.getcwd())
        if top:
            env["GIT_WORK_TREE"] = top
    return env


@functools.lru_cache(maxsize=None)
def hook_work_tree(git_directory, directory):
    env = {key: value for key, value in os.environ.items() if key not in HOOK_GIT_VARIABLES}
    result = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=directory, capture_output=True,
                            env=dict(env, GIT_DIR=git_directory))
    return os.fsdecode(result.stdout).strip() if not result.returncode else None


def checkout(directory, env):
    """(work tree, common Git directory) of the checkout holding directory, or None outside one."""
    result = subprocess.run(["git", "-C", str(directory), "rev-parse", "--show-toplevel",
                             "--path-format=absolute", "--git-common-dir"], capture_output=True, env=env)
    lines = os.fsdecode(result.stdout).splitlines()
    if result.returncode or len(lines) != 2:
        return None
    return Path(lines[0]).resolve(), Path(lines[1]).resolve()


def in_committing_checkout(project):
    """The project as seen by a commit in a linked worktree of the schema's repository.

    The settings and the DB stay the main checkout's (a worktree has no env/local.env of its own),
    but the snapshot is exported into and compared with the committing worktree's copy and index.
    Outside such a worktree the project is returned unchanged."""
    directory = schema_directory(project)
    ancestor = directory
    while not ancestor.exists():
        ancestor = ancestor.parent
    schema = checkout(ancestor, git_env(clean=True))
    committing = checkout(Path.cwd(), git_env())
    if (schema is None or committing is None or committing == schema or committing[1] != schema[1]
            or not directory.is_relative_to(schema[0])):
        return project
    project = copy.copy(project)
    project.committing_schema_directory = committing[0] / directory.relative_to(schema[0])
    print(f"Committing in the worktree {committing[0]}; DB and settings from {schema[0]}.")
    return project


def git(directory, *args):
    result = subprocess.run(["git", "-C", str(directory), *args], capture_output=True, env=git_env())
    if result.returncode:
        raise ValueError("Git schema operation failed; the schema directory must belong to the repository being committed")
    return result.stdout


def schema_repository(directory):
    ancestor = directory
    while not ancestor.exists():
        ancestor = ancestor.parent
    root = Path(os.fsdecode(git(ancestor, "rev-parse", "--show-toplevel")).strip()).resolve()
    if not directory.is_relative_to(root):
        raise ValueError("Schema directory is outside its Git repository")
    return root, directory.relative_to(root).as_posix() + "/"


def staged_schema(directory):
    root, prefix = schema_repository(directory)
    files = {}
    records = git(root, "ls-files", "--stage", "-z", "--", ":(literal)" + prefix)
    for record in records.split(b"\0"):
        if not record:
            continue
        metadata, pathname = record.split(b"\t", 1)
        mode, oid, stage = metadata.decode().split()
        name = os.fsdecode(pathname)[len(prefix):]
        if not managed_file(name):
            continue
        if stage != "0" or mode != "100644":
            raise ValueError(f"Schema file must be a regular resolved Git file: {name}")
        files[name] = git(root, "cat-file", "blob", oid)
    return files


def schema_differences(staged, actual):
    """File differences between two snapshots; a file that only changed directory (the layout) is a move."""
    differences = []
    added = sorted(actual.keys() - staged.keys())
    removed = sorted(staged.keys() - actual.keys())
    moved = {}
    for name in added:
        for old in removed:
            if old.rpartition("/")[2] == name.rpartition("/")[2] and staged[old] == actual[name] and old not in moved.values():
                moved[name] = old
                break
    for name in sorted(actual.keys() | staged.keys()):
        if name in moved:
            differences.append(f"moved: {moved[name]} -> {name}")
        elif name in moved.values():
            continue
        elif name not in staged:
            differences.append(f"not staged: {name}")
        elif name not in actual:
            differences.append(f"no longer in DB: {name}")
        elif staged[name] != actual[name]:
            differences.append(f"different: {name}")
    return differences


def commit_schema(project):
    """Pre-commit: export the schema, require classification and a finished migration, stop if anything is open."""
    from database_migrations_guarded import (commit_problems, migrations_enabled, record_applied, require_applied,
                                             staged_migrations, write_drafts)
    if not project.settings.get("SCHEMA_DIRECTORY"):
        print("Database as code is off (DATABASE_AS_CODE=off); the schema hook checks nothing.")
        return
    project = in_committing_checkout(project)
    directory = schema_directory(project)
    try:
        database_tables(project)
    except ValueError:
        print("Warning: the database is not reachable; the schema was not checked for this commit.")
        return
    migrations = migrations_enabled(project)
    if migrations:
        require_applied(project)  # Pulled migrations not applied here: an export would undo them.
    actual = read_schema(project, require_classified=True)  # The extra schemas' tables included.
    export_schema(project, actual)
    staged = staged_schema(directory)
    problems = [f"schema {line}" for line in schema_differences(staged, actual)]
    problems += commit_problems(project, staged if not problems else actual)
    if problems:
        drafts = write_drafts(project, actual) if migrations else []
        raise ValueError("The commit was stopped for review:\n  " + "\n  ".join(problems)
                         + "".join(f"\nGuarded migration draft: {draft}" for draft in drafts)
                         + f"\nReview, then git add -A {directory.parent} and commit again.")
    if migrations:
        # The new migrations describe the change this database already has: record them as applied here.
        try:
            record_applied(project, staged_migrations(project))
        except ValueError:
            print("Warning: the new migrations were not recorded as applied here; make db-migrate records them "
                  "(their guarded statements change nothing).")
    print(f"Schema matches the commit ({len(actual) - 1} tables).")


def schema_report(project):
    """After a load: warn where the DB differs from the branch snapshot; never fails the load."""
    directory = schema_directory(project)
    if not (directory / MANIFEST).is_file():
        return []
    try:
        actual = read_schema(project)
    except ValueError as error:
        print(f"Warning: schema report skipped: {error}")
        return []
    # By table name through the manifest: a server's DATABASE_NAME may differ from the one the layout was exported with.
    from database_migrations import definitions
    snapshot = definitions({name: path.read_bytes() for name, path in snapshot_files(directory).items()})
    current = definitions(actual)
    differences = [f"only in the DB: {table}" if table not in snapshot else f"missing in the DB: {table}" if table not in current
                   else f"different: {table}" for table in sorted(snapshot.keys() | current.keys())
                   if snapshot.get(table) != current.get(table)]
    if not differences:
        print(f"Schema matches the branch snapshot ({len(current)} tables).")
        return []
    print(f"Warning: the DB differs from the branch snapshot in {len(differences)} places "
          "(tables only in the DB came with the dump: LIVE-only, or not yet in this branch):")
    for line in differences:
        print("  " + line)
    return differences


def check_schema(project):
    project = in_committing_checkout(project)  # A hook manager may run it for a worktree's commit.
    directory = schema_directory(project)
    staged = staged_schema(directory)
    actual = read_schema(project)
    differences = schema_differences(staged, actual)
    if differences:
        raise ValueError("DB schema differs from the Git index:\n  " + "\n  ".join(differences)
                         + "\nRun make schema-export, review the changes, then git add -A the schema directory.")
    print(f"Schema matches the Git index ({len(actual) - 1} tables).")


def install_schema_hook(project):
    """make schema-hook-install: the pre-commit hook (git_hooks.py), whose first part is schema-commit."""
    schema_directory(project)  # refuses DATABASE_AS_CODE=off
    from git_hooks import install_git_hooks
    install_git_hooks(project, ("pre-commit",))
