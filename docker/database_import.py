"""Stream an explicit SQL dump, then ordered project hooks, to its Compose DB."""

from contextlib import ExitStack
import fcntl
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


from database_client import IMPORT as MYSQL_IMPORT
from database_backup import backup_database
import gzip
import shutil


DOCKER_ROOT = Path(__file__).resolve().parent
# INSERT/REPLACE statements of mysqldump and phpMyAdmin dumps; the table name is the first quoted name.
CREATE = re.compile(rb"^CREATE\s+(?:TEMPORARY\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`((?:[^`]|``)+)`", re.I)
COLUMN = re.compile(rb"^\s*[(,]?\s*`((?:[^`]|``)+)`\s+[a-zA-Z]")
INSERT = re.compile(rb"^(?:INSERT|REPLACE)(?:\s+(?:IGNORE|LOW_PRIORITY|DELAYED|HIGH_PRIORITY))*\s+(?:INTO\s+)?`((?:[^`]|``)+)`", re.I)


def hook_values(project):
    """Values an after-import hook may use; each is validated so it cannot carry SQL or shell syntax."""
    domain = project.settings.get("SQL_DOMAIN") or project.settings.get("DOMAIN", "")
    prefix = project.settings.get("PS_DATABASE_PREFIX") or "ps_"
    return {
        "DOMAIN": (domain, r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?(?::[0-9]{1,5})?", "a valid DOMAIN hostname"),
        "DB_PREFIX": (prefix, r"[A-Za-z0-9_]+", "PS_DATABASE_PREFIX of letters, digits and _"),
        "PS_SSL": ("0" if project.settings.get("PS_SSL") == "off" else "1", r"[01]", "PS_SSL on or off"),
    }


def render_hook(project, path):
    contents = path.read_bytes()
    for name, (value, pattern, description) in hook_values(project).items():
        token = ("${" + name + "}").encode()
        if token in contents:
            if not re.fullmatch(pattern, value):
                raise ValueError(f"SQL hooks using ${{{name}}} require {description}")
            contents = contents.replace(token, value.encode("ascii"))
    return contents


def skip_rule(project, full=False):
    """Tables whose LIVE data the registry says not to import, or None to import everything."""
    if full or not project.settings.get("SCHEMA_DIRECTORY"):
        return None
    from database_registry import classify, load
    entries = load(project)
    if not entries:
        return None
    return lambda table: (classify(entries, table) or ("", "", "", "import"))[3] in ("skip", "-")


def dump_tables(path):
    """Tables a dump creates, with their column names (read from the CREATE TABLE statements)."""
    tables, current = {}, None
    with (gzip.open(path, "rb") if path.name.lower().endswith(".gz") else path.open("rb")) as source:
        for line in source:
            if line[:1] in (b"C", b"c") and (match := CREATE.match(line)):
                current = match[1].replace(b"``", b"`").decode("utf-8", "replace")
                tables[current] = set(COLUMN.findall(line[match.end():]))
                if line.rstrip().endswith(b";"):
                    current = None
            elif current is not None:
                if column := COLUMN.match(line):
                    tables[current].add(column[1])
                if line.rstrip().endswith(b";"):
                    current = None
    return {table: {column.decode("utf-8", "replace") for column in columns} for table, columns in tables.items()}


def check_dump_tables(project, dump, ask=input, interactive=None):
    """An unclassified table stops the import: classify it in a terminal, or fail with suggestions."""
    if not project.settings.get("SCHEMA_DIRECTORY"):
        return
    from database_registry import load, resolve, unknown_tables
    entries = load(project)
    if not entries:
        return
    tables = dump_tables(Path(dump))
    unknown = unknown_tables(entries, tables)
    if not unknown:
        return
    print(f"The dump has tables the registry does not classify ({len(unknown)}); the import stops before loading anything.")
    resolve(project, ask, interactive, tables=unknown, columns={table: tables[table] for table in unknown})
    left = unknown_tables(load(project), unknown)
    if left:
        raise ValueError(f"{len(left)} tables are not classified: {', '.join(left[:10])}")


def filter_dump(source, sink, skip):
    """Copy a dump without the data statements of skipped tables; returns the skipped table names.

    Structure statements are kept, so a skipped table exists and is empty. A multi-line INSERT is
    skipped up to the line that ends the statement.
    """
    skipped, skipping = set(), False
    for line in source:
        if skipping:
            skipping = not line.rstrip().endswith(b";")
            continue
        if line[:1] in (b"I", b"i", b"R", b"r") and (match := INSERT.match(line)):
            table = match[1].replace(b"``", b"`").decode("utf-8", "replace")
            if skip(table):
                skipped.add(table)
                skipping = not line.rstrip().endswith(b";")
                continue
        sink.write(line)
    return skipped


def progress_meter():
    """pv for the dump when a person watches the terminal; plain streaming otherwise."""
    return shutil.which("pv") if sys.stderr.isatty() else None


def stream_sql(project, command, handle, meter=None):
    if not meter:
        subprocess.run(command, env=project.child_env(), stdin=handle, check=True)
        return
    size = os.fstat(handle.fileno()).st_size - handle.tell()
    progress = subprocess.Popen([meter, "-s", str(size)], stdin=handle, stdout=subprocess.PIPE)
    try:
        subprocess.run(command, env=project.child_env(), stdin=progress.stdout, check=True)
    finally:
        # Closing our read end lets pv stop on EPIPE if the DB client exited early.
        progress.stdout.close()
        status = progress.wait()
    if status:
        raise subprocess.CalledProcessError(status, [meter])


def stream_filtered(project, command, handle, skip, meter=None):
    progress = None
    if meter:
        size = os.fstat(handle.fileno()).st_size - handle.tell()
        progress = subprocess.Popen([meter, "-s", str(size)], stdin=handle, stdout=subprocess.PIPE)
    client = subprocess.Popen(command, env=project.child_env(), stdin=subprocess.PIPE)
    skipped = set()
    try:
        skipped = filter_dump(progress.stdout if progress else handle, client.stdin, skip)
    except BrokenPipeError:
        pass  # The client stopped early; its exit status reports why.
    finally:
        try:
            client.stdin.close()
        except BrokenPipeError:
            pass
        status = client.wait()
        if progress:
            progress.stdout.close()
            meter_status = progress.wait()
    if status:
        raise subprocess.CalledProcessError(status, command[:1])
    if progress and meter_status:
        raise subprocess.CalledProcessError(meter_status, [meter])
    return skipped


def after_import_hooks(project):
    """SQL run after any DB load: profile safe defaults, project common and environment scripts, integration switches."""
    configured = project.settings.get("POST_IMPORT_SQL_DIRECTORY", "")
    # Unset means the shared convention beside the project's Makefile; it may be absent.
    directory = (project.root / configured).resolve() if configured else (project.directory / "database/after-import").resolve()
    if not directory.is_relative_to(project.root):
        raise ValueError("POST_IMPORT_SQL_DIRECTORY must be a directory inside the project")
    if configured and not directory.is_dir():
        raise ValueError("POST_IMPORT_SQL_DIRECTORY must be a directory inside the project")
    hooks = []
    safe = project.environment != "prod"
    profile = DOCKER_ROOT / "database-profile/prestashop/after-import"
    if safe and project.settings.get("PROFILE") in ("ps", "prestashop"):
        # Safe defaults first (shop URL, payment sandboxes), so project scripts can refine them.
        hooks += sorted(profile.glob("*.sql"))
    for group in ("common", project.environment) if directory.is_dir() else ():
        for path in sorted((directory / group).glob("*.sql")):
            resolved = path.resolve()
            if not resolved.is_relative_to(directory) or not resolved.is_file():
                raise ValueError(f"SQL hook must be a file inside {directory}: {path.name}")
            hooks.append(resolved)
    integrations = {path.stem: path for path in sorted((directory / "integrations").glob("*.sql"))} if directory.is_dir() else {}
    enabled = str(project.settings.get("INTEGRATIONS_ENABLED") or "").split()
    unknown = sorted(set(enabled) - integrations.keys())
    if unknown:
        raise ValueError(f"INTEGRATIONS_ENABLED names unknown integrations: {', '.join(unknown)}; "
                         f"available: {', '.join(integrations) or 'none'}")
    for name, path in integrations.items() if safe else ():
        # Each script switches one external integration off; INTEGRATIONS_ENABLED keeps it on.
        resolved = path.resolve()
        if not resolved.is_relative_to(directory) or not resolved.is_file():
            raise ValueError(f"SQL hook must be a file inside {directory}: {path.name}")
        if name not in enabled:
            hooks.append(resolved)
    return hooks


def plan_import(project, dump):
    if not dump:
        raise ValueError("Specify a dump with file=/path/to/dump.sql")
    source = Path(dump).resolve()
    if not (source.name.lower().endswith(".sql") or source.name.lower().endswith(".sql.gz")) or not source.is_file() or source.stat().st_size == 0:
        raise ValueError("The dump must be a readable, nonempty .sql or .sql.gz file")
    if not project.settings["DATABASE_NAME"]:
        raise ValueError("DATABASE_NAME is required for import")
    hooks = after_import_hooks(project)
    plan = [source, *hooks]
    # Preflight every input before any database operation, including hook readability.
    for path in plan:
        with path.open("rb"):
            pass
    if source.name.lower().endswith(".sql.gz"):
        try:
            size = 0
            with gzip.open(source, "rb") as compressed:
                while block := compressed.read(1024 * 1024):
                    size += len(block)
            if not size:
                raise ValueError("The compressed SQL dump is empty")
        except (OSError, EOFError) as error:
            raise ValueError("The compressed SQL dump is invalid or truncated") from error
    for path in hooks:
        render_hook(project, path)
    return plan


def import_database(project, plan, backup=False, full=False):
    """Dump (without skipped table data) -> after-import SQL -> migrations -> shop policy -> schema report."""
    skipped = execute_sql(project, plan, first_is_dump=True, backup=backup, skip=skip_rule(project, full))
    if skipped:
        print(f"Data not imported for {len(skipped)} tables (registry live rule skip; full=1 imports all): "
              + ", ".join(sorted(skipped)[:10]) + (" ..." if len(skipped) > 10 else ""))
    after_load(project)
    print("Dump and after-import SQL completed.")


def after_load(project):
    """Everything that follows loading data: pending migrations, shop policy and a schema report."""
    if project.settings.get("SCHEMA_DIRECTORY"):
        from database_migrations_guarded import migrate, migrations_directory
        if migrations_directory(project).is_dir():
            migrate(project)
    if project.settings.get('PROFILE') in ('ps', 'prestashop'):
        running = project.capture(['ps', '--status', 'running', '--services']).split()
        if 'php-fpm' in running:
            # A restored dump may bring back cache/mail settings from production.
            # If PHP is stopped, its next startup performs this preparation instead.
            project.capture(['exec', '-T', 'php-fpm', 'php', '/opt/setup/runtime/prestashop-command.php', 'policy'])
            project.capture(['exec', '-T', 'php-fpm', 'php', '/opt/setup/runtime/prestashop-command.php', 'cache-clear'])
            project.run(['restart', 'php-fpm'])
    if project.settings.get("SCHEMA_DIRECTORY"):
        from database_schema import schema_report
        schema_report(project)


def execute_sql(project, plan, *, first_is_dump=False, backup=False, skip=None):
    """Pre-open all inputs and serialize imports/fixtures with the same DB lock; returns skipped tables."""
    output = project.directory / ".generated"
    output.mkdir(mode=0o700, exist_ok=True)
    if first_is_dump:
        from project_storage import require_space
        size = plan[0].stat().st_size
        if plan[0].name.lower().endswith('.gz'):
            size = 0
            with gzip.open(plan[0], 'rb') as stream:
                while block := stream.read(1024 * 1024): size += len(block)
            require_space(project, output, size)
        require_space(project, getattr(project, 'data_directory', output), 2 * size)
    lock_path = output / f"db-import.{project.environment}.lock"
    with os.fdopen(os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600), "r+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("An import for this project/environment is already running") from error
        with ExitStack() as stack:
            inputs = []
            for index, path in enumerate(plan):
                if index == 0 and first_is_dump:
                    if path.name.lower().endswith(".sql.gz"):
                        handle = stack.enter_context(tempfile.TemporaryFile(dir=output))
                        with gzip.open(path, "rb") as compressed:
                            shutil.copyfileobj(compressed, handle)
                        handle.seek(0)
                    else:
                        handle = stack.enter_context(path.open("rb"))
                else:
                    # subprocess stdin requires a real file descriptor. Keep rendered SQL
                    # in a private temporary file inside this repository, never in sources.
                    handle = stack.enter_context(tempfile.TemporaryFile(dir=output))
                    handle.write(render_hook(project, path))
                    handle.seek(0)
                inputs.append((path, handle))
            if backup:
                backup_database(project)
            command = project.command + ["exec", "-T", "database", "sh", "-c", MYSQL_IMPORT,
                                         "database-import", project.settings["DATABASE_NAME"]]
            meter = progress_meter() if first_is_dump else None
            skipped = set()
            for index, (path, handle) in enumerate(inputs):
                dump = index == 0 and first_is_dump
                print(f"{'Import' if dump else 'SQL'}: {path}", flush=True)
                # A failing dump or hook stops the sequence; no later SQL runs.
                if dump and skip:
                    skipped = stream_filtered(project, command, handle, skip, meter)
                else:
                    stream_sql(project, command, handle, meter if dump else None)
            return skipped
