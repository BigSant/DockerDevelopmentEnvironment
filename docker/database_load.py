"""The Load: data into a project database from a dump, a data set or fixtures, as one locked, pre-checked run.

plan_load reads and checks every input without writing to the database; run_load holds the import lock from
the table check to the after-load steps (pending migrations, shop policy, schema report)."""

import contextlib
from dataclasses import dataclass, field
import fcntl
import os
from pathlib import Path
import tempfile
from typing import Callable, Optional

from database_client import DatabaseError, extra_schemas
from database_import import (CHUNK, after_import_hooks, check_dump_tables, load_dump, main_schema, progress_meter,
                             render_hook, scan_dump, skip_rule, unpack)
from project_environments import is_server
from project_prestashop import is_prestashop


@dataclass
class LoadPlan:
    """What a load will do, checked: kind is dump, dataset or fixtures."""
    kind: str
    name: str = ''                      # the data set or fixture set
    dump: Optional[Path] = None
    data: list = field(default_factory=list)       # data set files, after the schema snapshot
    schema: bytes = b''                 # the data set's CREATE TABLE statements
    hooks: list = field(default_factory=list)      # after-import SQL
    fixtures: list = field(default_factory=list)
    skip: Optional[Callable] = None     # tables whose dump data stays out
    replace: bool = False
    size: int = 0                       # bytes of data the database receives

    def paths(self):
        """Every file the load reads, in order: the dump or data set files, after-import SQL, fixtures."""
        return [*([self.dump] if self.dump else self.data), *self.hooks, *self.fixtures]

    def lines(self, project):
        """The plan as make db-import-plan / db-fixtures-plan print it."""
        context = f"Database: {project.settings['DATABASE_NAME']}; environment: {project.environment}"
        if self.kind == 'fixtures':
            context += f'; fixture set: {self.name}'
            return [context, *map(str, self.fixtures)] if self.fixtures else [context, 'No SQL fixtures in the selected set.']
        return [context, *map(str, self.paths())]


def plan_load(project, dump=None, dataset=None, fixtures=None, skip=False, replace=False, ask=input,
              interactive=None):
    """Check a load before it touches the database: a dump (with optional fixtures), a data set (with its
    fixtures) or fixtures alone. Every file is read, every hook rendered and every dump table classified."""
    if dump is not None and dataset is not None:
        raise ValueError('Load a dump or a data set, not both')
    if not project.settings['DATABASE_NAME']:
        raise ValueError('DATABASE_NAME is required for a load')
    if dump is not None:
        plan = plan_dump(project, dump, ask, interactive)
        plan.skip = skip_rule(project) if skip else None
        if fixtures is not None:
            from database_fixtures import plan_fixtures
            plan.fixtures = plan_fixtures(project, fixtures)
    elif dataset is not None:
        if fixtures is not None:
            raise ValueError('A data set brings its own fixtures (database/fixtures/<set> or common)')
        plan = plan_dataset(project, dataset, replace)
    else:
        from database_fixtures import plan_fixtures
        plan = LoadPlan('fixtures', name=fixtures, fixtures=plan_fixtures(project, fixtures))
    for path in [*plan.hooks, *plan.fixtures]:
        render_hook(project, path)  # Read and validate every file before database work.
    if plan.kind != 'fixtures':
        from project_storage import require_space
        output = project.directory / '.generated'
        if plan.dump and plan.dump.name.lower().endswith('.gz'):
            require_space(project, output, plan.size)  # The dump is unpacked there first.
        require_space(project, getattr(project, 'data_directory', output), 2 * plan.size)
    return plan


def plan_dump(project, dump, ask=input, interactive=None):
    if not dump:
        raise ValueError("Specify a dump with file=/path/to/dump.sql")
    source = Path(dump).resolve()
    if not (source.name.lower().endswith(".sql") or source.name.lower().endswith(".sql.gz")) or not source.is_file() or source.stat().st_size == 0:
        raise ValueError("The dump must be a readable, nonempty .sql or .sql.gz file")
    hooks = after_import_hooks(project)
    # Preflight every input before any database operation, including hook readability.
    for path in [source, *hooks]:
        with path.open("rb"):
            pass
    scan = scan_dump(source, main_schema(project), progress_meter())
    if source.name.lower().endswith(".sql.gz") and not scan["size"]:
        raise ValueError("The compressed SQL dump is empty")
    extras = extra_schemas(project.settings)
    if extras:
        # A multi-schema dump restores each schema under its own name; refuse one that would write elsewhere.
        foreign = sorted(scan["schemas"] - {project.settings["DATABASE_NAME"], *extras})
        if foreign:
            raise ValueError(f"The dump switches to schemas outside DATABASE_NAME and DATABASE_EXTRA_SCHEMAS: "
                             f"{', '.join(foreign)}; nothing was imported")
    # Every dump table must be classified before anything is loaded; interactive may ask for it.
    check_dump_tables(project, source, ask, interactive)
    return LoadPlan('dump', dump=source, hooks=hooks, size=scan["size"] or source.stat().st_size)


def plan_dataset(project, name, replace):
    from database_dataset import dataset_inputs, schema_sql
    if is_server(project.environment):
        raise ValueError('The base data set (dev logins, sample data) loads only into local and test databases')
    files, fixtures = dataset_inputs(project, name)
    schema = schema_sql(project)
    return LoadPlan('dataset', name=name, data=files, schema=schema, hooks=after_import_hooks(project),
                    fixtures=fixtures, replace=replace, size=len(schema) + sum(path.stat().st_size for path in files))


@contextlib.contextmanager
def import_lock(project):
    """One load per project and environment at a time; a second one fails at once."""
    output = project.directory / ".generated"
    output.mkdir(mode=0o700, exist_ok=True)
    with os.fdopen(os.open(output / f"db-import.{project.environment}.lock", os.O_CREAT | os.O_RDWR, 0o600), "r+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("An import for this project/environment is already running") from error
        yield output


def run_load(project, plan, backup=False):
    """Under the lock: table check, backup, drop, data, after-import SQL and fixtures, then the after-load steps
    (not for fixtures alone). A failing step stops the run; no later SQL runs. Returns the tables skipped."""
    if plan.kind == 'fixtures' and not plan.fixtures:
        print("No SQL fixtures in the selected set; database unchanged.")
        return set()
    from database_backup import backup_database
    from database_schema import database_tables
    with import_lock(project) as output, contextlib.ExitStack() as stack:
        dump = None
        if plan.kind == 'dump':
            if plan.dump.name.lower().endswith(".sql.gz"):
                dump = stack.enter_context(tempfile.TemporaryFile(dir=output))
                unpack(plan.dump, dump, progress_meter())
                dump.seek(0)
            else:
                dump = stack.enter_context(plan.dump.open("rb"))
        sql = [(path, render_hook(project, path)) for path in [*plan.hooks, *plan.fixtures]]
        existing = database_tables(project) if plan.kind == 'dataset' else []
        if existing and not plan.replace:
            raise ValueError(f'Database {project.settings["DATABASE_NAME"]} has {len(existing)} tables; '
                             'replace=1 drops them first (add backup=1 to keep a copy)')
        if backup and (plan.kind != 'dataset' or existing):
            backup_database(project)
        if existing:
            from database_dataset import drop_all_tables
            drop_all_tables(project)
        skipped = set()
        if plan.kind == 'dump':
            print(f"Import: {plan.dump}", flush=True)
            skipped = load_dump(project, dump, plan.skip, progress_meter())
        elif plan.kind == 'dataset':
            print(f"Data set {plan.name}: schema snapshot and {len(plan.data)} files", flush=True)
            load_files(project, [plan.schema, *plan.data], plan.size, progress_meter())
        for path, text in sql:
            print(f"SQL: {path}", flush=True)
            with project.database.load() as sink:
                sink.write(text)
        if skipped:
            print(f"Data not imported for {len(skipped)} tables (registry live rule skip; skip=1 was given): "
                  + ", ".join(sorted(skipped)[:10]) + (" ..." if len(skipped) > 10 else ""))
        if plan.kind != 'fixtures':
            after_load(project)
            # The loaded translations are where the next translation migration is drafted from.
            from project_translations import database_loaded
            database_loaded(project)
    if plan.kind == 'dump':
        print("Dump and after-import SQL completed.")
    elif plan.kind == 'dataset':
        from database_dataset import dataset_notes
        print('\n'.join(dataset_notes(project, plan.name)))
    else:
        print("SQL fixtures completed.")
    return skipped


def load_files(project, parts, size, meter=None):
    """Bytes and files, in order, as one load with one progress bar."""
    bar = meter("Import", size) if meter else None
    try:
        with project.database.load() as sink:
            for part in parts:
                if isinstance(part, bytes):
                    sink.write(part)
                    if bar:
                        bar.advance(len(part))
                    continue
                with open(part, 'rb') as handle:
                    while block := handle.read(CHUNK):
                        sink.write(block)
                        if bar:
                            bar.advance(len(block))
    except DatabaseError:
        if bar:
            bar.finish("failed")
        raise
    if bar:
        bar.finish("")


def after_load(project):
    """Everything that follows loading data: pending migrations, shop policy and a schema report."""
    from database_migrations_guarded import migrate, migrations_directory, migrations_enabled
    if migrations_enabled(project) and migrations_directory(project).is_dir():
        migrate(project)
    if is_prestashop(project):
        running = project.capture(['ps', '--status', 'running', '--services']).split()
        if 'php-fpm' in running:
            # A restored dump may bring back cache/mail settings from production.
            # If PHP is stopped, its next startup performs this preparation instead.
            project.capture(['exec', '-T', 'php-fpm', 'php', '/opt/setup/runtime/prestashop-command.php', 'policy'])
            project.capture(['exec', '-T', 'php-fpm', 'php', '/opt/setup/runtime/prestashop-command.php', 'cache-clear'])
            project.run(['restart', 'php-fpm'])
            # Outside prod the load gave the shops their local domains: the old .htaccess rewrites the old ones.
            from project_prestashop import ensure_htaccess
            ensure_htaccess(project, regenerate=project.environment != 'prod')
    if project.settings.get("SCHEMA_DIRECTORY"):
        from database_schema import schema_report
        schema_report(project)
