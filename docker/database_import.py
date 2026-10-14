"""Stream an explicit SQL dump, then ordered project hooks, to its Compose DB."""

from contextlib import ExitStack
import fcntl
import os
from pathlib import Path
import re
import sys
import tempfile


from database_client import DatabaseError, extra_schemas
from database_backup import backup_database
import gzip
from project_prestashop import is_prestashop


DOCKER_ROOT = Path(__file__).resolve().parent
CHUNK = 1024 * 1024
# What one reading of a dump found (DumpScanner: size, schemas, tables), by (path, size, mtime_ns, main schema).
# db-pull fills it while it downloads, so an import right after it does not read a large file again; without
# it the load plan and the registry check share one reading instead of three.
SCANS = {}
# INSERT/REPLACE statements of mysqldump and phpMyAdmin dumps; the table name is the first quoted name.
CREATE = re.compile(rb"^CREATE\s+(?:TEMPORARY\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`((?:[^`]|``)+)`", re.I)
COLUMN = re.compile(rb"^\s*[(,]?\s*`((?:[^`]|``)+)`\s+[a-zA-Z]")
INSERT = re.compile(rb"^(?:INSERT|REPLACE)(?:\s+(?:IGNORE|LOW_PRIORITY|DELAYED|HIGH_PRIORITY))*\s+(?:INTO\s+)?`((?:[^`]|``)+)`", re.I)
# A multi-schema dump (mysqldump --databases) switches schemas with USE; a single-schema dump has none.
USE = re.compile(rb"^USE\s+(?:`((?:[^`]|``)+)`|([A-Za-z0-9_$]+))\s*;", re.I)


def used_schema(line):
    """The schema a USE line switches to, or None for any other line."""
    if line[:1] in (b"U", b"u") and (match := USE.match(line)):
        return (match[1].replace(b"``", b"`") if match[1] is not None else match[2]).decode("utf-8", "replace")
    return None


def main_schema(project):
    """With DATABASE_EXTRA_SCHEMAS, the main database name: registry rules apply only to its part of a
    dump (before any USE, or after USE of it). None keeps the single-schema reading."""
    return project.settings["DATABASE_NAME"] if extra_schemas(project.settings) else None


# Hook values of the PrestaShop profile; a generic project's SQL has no table prefix or shop SSL flag.
PRESTASHOP_HOOK_VALUES = ("DB_PREFIX", "PS_SSL")


def hook_values(project):
    """Values an after-import hook may use; each is validated so it cannot carry SQL or shell syntax."""
    domain = project.settings.get("SQL_DOMAIN") or project.settings.get("DOMAIN", "")
    values = {"DOMAIN": (domain, r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?(?::[0-9]{1,5})?", "a valid DOMAIN hostname")}
    # The DOMAIN_<NAME> variables DOMAIN lists (project_domains); setup itself never writes them anywhere.
    for name, host in (project.settings.get("DOMAIN_REFERENCES") or {}).items():
        values[name] = (host, r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", f"{name} to be a hostname")
    if is_prestashop(project):
        prefix = project.settings.get("PS_DATABASE_PREFIX") or "ps_"
        values["DB_PREFIX"] = (prefix, r"[A-Za-z0-9_]+", "PS_DATABASE_PREFIX of letters, digits and _")
        values["PS_SSL"] = ("0" if project.settings.get("PS_SSL") == "off" else "1", r"[01]", "PS_SSL on or off")
    return values


def after_import_directory(project):
    configured = project.settings.get("POST_IMPORT_SQL_DIRECTORY", "")
    # Unset means the shared convention beside the project's Makefile; it may be absent.
    directory = (project.root / configured).resolve() if configured else (project.directory / "database/after-import").resolve()
    if not directory.is_relative_to(project.root):
        raise ValueError("POST_IMPORT_SQL_DIRECTORY must be a directory inside the project")
    if configured and not directory.is_dir():
        raise ValueError("POST_IMPORT_SQL_DIRECTORY must be a directory inside the project")
    return directory


def hook_schema(project, path):
    """The extra schema an after-import script runs in: <group>/<schema>/*.sql; None for the main database."""
    if path.parent.name not in extra_schemas(project.settings):
        return None
    group = path.parent.parent
    if group.parent == after_import_directory(project) and group.name in ("common", project.environment):
        return path.parent.name
    return None


def render_hook(project, path):
    contents = path.read_bytes()
    values = hook_values(project)
    for name in PRESTASHOP_HOOK_VALUES:
        if name not in values and ("${" + name + "}").encode() in contents:
            from project_prestashop import generic_refusal
            raise generic_refusal(f"${{{name}}} in {path.name}")
    for name, (value, pattern, description) in values.items():
        token = ("${" + name + "}").encode()
        if token in contents:
            if not re.fullmatch(pattern, value):
                raise ValueError(f"SQL hooks using ${{{name}}} require {description}")
            contents = contents.replace(token, value.encode("ascii"))
    schema = hook_schema(project, path)
    return (f"USE `{schema}`;\n".encode() if schema else b"") + contents


def skip_rule(project):
    """Tables whose data the registry leaves out of a backup (live rule skip), or None for every table."""
    if not project.settings.get("SCHEMA_DIRECTORY"):
        return None
    from database_registry import classify, load
    entries = load(project)
    if not entries:
        return None
    return lambda table: (classify(entries, table) or ("", "", "", "import"))[3] in ("skip", "-")


class DumpScanner:
    """Reads a dump once, as it streams by: its uncompressed size, the schemas it switches to (USE) and the
    tables it creates with their columns (CREATE TABLE). With `main`, the main database's tables are plain
    names and the other schemas' are schema.table (the table registry's names); `qualified=False` keeps the
    main database's only. feed() takes bytes in any pieces; result() ends the reading."""

    def __init__(self, main=None, qualified=True):
        self.main, self.qualified = main, qualified
        self.size, self.pending, self.schemas = 0, b"", set()
        self.tables, self.current, self.schema = {}, None, main

    def feed(self, data):
        self.size += len(data)
        lines = (self.pending + data).split(b"\n")
        self.pending = lines.pop()
        for line in lines:
            self.line(line + b"\n")

    def line(self, line):
        if (used := used_schema(line)) is not None:
            self.schemas.add(used)
            if self.main is not None:
                self.schema = used
        if self.schema != self.main and not self.qualified:
            return
        if line[:1] in (b"C", b"c") and (match := CREATE.match(line)):
            current = match[1].replace(b"``", b"`").decode("utf-8", "replace")
            if self.schema != self.main:
                current = f"{self.schema}.{current}"
            self.tables[current] = set(COLUMN.findall(line[match.end():]))
            self.current = None if line.rstrip().endswith(b";") else current
        elif self.current is not None:
            if column := COLUMN.match(line):
                self.tables[self.current].add(column[1])
            if line.rstrip().endswith(b";"):
                self.current = None

    def result(self):
        if self.pending:
            self.line(self.pending)
            self.pending = b""
        tables = {table: {column.decode("utf-8", "replace") for column in columns} for table, columns in self.tables.items()}
        return {"size": self.size, "schemas": set(self.schemas), "tables": tables}


def check_dump_tables(project, dump, ask=input, interactive=None):
    """An unclassified table stops the import: classify it in a terminal, or fail with suggestions."""
    if not project.settings.get("SCHEMA_DIRECTORY"):
        return
    from database_registry import load, resolve, unknown_tables
    entries = load(project)
    if not entries:
        return
    tables = scan_dump(Path(dump), main_schema(project), progress_meter())["tables"]
    unknown = unknown_tables(entries, tables)
    if not unknown:
        return
    print(f"The dump has tables the registry does not classify ({len(unknown)}); the import stops before loading anything.")
    resolve(project, ask, interactive, tables=unknown, columns={table: tables[table] for table in unknown})
    left = unknown_tables(load(project), unknown)
    if left:
        raise ValueError(f"{len(left)} tables are not classified: {', '.join(left[:10])}")


def filter_dump(source, sink, skip, main=None):
    """Copy a dump without the data statements of skipped tables; returns the skipped table names.

    Structure statements are kept, so a skipped table exists and is empty. A multi-line INSERT is
    skipped up to the line that ends the statement. With `main`, a table of another schema is asked about
    as schema.table, the table registry's name for it.
    """
    skipped, skipping, schema = set(), False, main
    for line in source:
        if skipping:
            skipping = not line.rstrip().endswith(b";")
            continue
        if main is not None and (used := used_schema(line)) is not None:
            schema = used
        if line[:1] in (b"I", b"i", b"R", b"r") and (match := INSERT.match(line)):
            table = match[1].replace(b"``", b"`").decode("utf-8", "replace")
            if schema != main:
                table = f"{schema}.{table}"
            if skip(table):
                skipped.add(table)
                skipping = not line.rstrip().endswith(b";")
                continue
        sink.write(line)
    return skipped


def progress_meter():
    """A progress bar factory (progress.Bar) for the dump when a person watches the terminal; None otherwise."""
    if not sys.stderr.isatty():
        return None
    from progress import Bar
    return lambda label, total: Bar(label, total)


def file_key(path, main=None):
    info = Path(path).stat()
    return (str(Path(path).resolve()), info.st_size, info.st_mtime_ns, main)


def remember_scan(path, main, scan):
    """Keep what a reading found (db-pull reads the dump while it downloads it)."""
    SCANS[file_key(path, main)] = scan


def scan_dump(path, main=None, meter=None):
    """{size, schemas, tables} of a dump, read once (for a .sql.gz the read is the integrity check too);
    raises ValueError for a broken archive."""
    path = Path(path)
    key = file_key(path, main)
    if key in SCANS:
        return SCANS[key]
    bar = meter("Check dump", key[1]) if meter else None
    scanner = DumpScanner(main, qualified=True)
    try:
        with open(path, "rb") as raw:
            source = gzip.GzipFile(fileobj=raw, mode="rb") if path.name.lower().endswith(".gz") else raw
            while block := source.read(CHUNK):
                scanner.feed(block)
                if bar:
                    bar.update(raw.tell())
    except (OSError, EOFError) as error:
        if bar:
            bar.finish("failed")
        raise ValueError("The compressed SQL dump is invalid or truncated") from error
    if bar:
        bar.finish()
    SCANS[key] = scanner.result()
    return SCANS[key]


def unpack(path, sink, meter=None):
    """Decompress a .sql.gz into sink, with a bar over the compressed bytes read."""
    total = Path(path).stat().st_size
    bar = meter("Unpack dump", total) if meter else None
    with open(path, "rb") as raw, gzip.GzipFile(fileobj=raw, mode="rb") as compressed:
        while block := compressed.read(CHUNK):
            sink.write(block)
            if bar:
                bar.update(raw.tell())
    if bar:
        bar.finish()


class Counted:
    """Lines of a file that move a progress bar as they are read (the filtered import)."""

    def __init__(self, handle, bar):
        self.handle, self.bar = handle, bar

    def __iter__(self):
        done = 0
        for line in self.handle:
            done += len(line)
            if done - self.bar.done >= CHUNK:
                self.bar.update(done)
            yield line
        self.bar.update(done)


def load_dump(project, handle, skip=None, meter=None):
    """Stream a dump into the database; skip leaves out the data of those tables. Returns the tables skipped."""
    bar = meter("Import", os.fstat(handle.fileno()).st_size - handle.tell()) if meter else None
    skipped = set()
    try:
        with project.database.load() as sink:
            if skip:
                skipped = filter_dump(Counted(handle, bar) if bar else handle, sink, skip, main_schema(project))
            else:
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
    return skipped


def after_import_hooks(project):
    """SQL run after any DB load: profile safe defaults, project common and environment scripts (an extra schema's
    from <group>/<schema>/), integration switches."""
    directory = after_import_directory(project)
    hooks = []
    safe = project.environment != "prod"
    profile = DOCKER_ROOT / "database-profile/prestashop/after-import"
    if safe and is_prestashop(project):
        # Safe defaults first (shop URL, payment sandboxes), so project scripts can refine them.
        hooks += sorted(profile.glob("*.sql"))
    extras = extra_schemas(project.settings)
    for group in ("common", project.environment) if directory.is_dir() else ():
        # The main database's scripts, then each extra schema's from its subdirectory (run in that schema).
        folders = [directory / group]
        if (directory / group).is_dir():
            for folder in sorted(path for path in (directory / group).iterdir() if path.is_dir()):
                if folder.name not in extras and any(folder.glob("*.sql")):
                    raise ValueError(f"after-import/{group}/{folder.name} is not a schema of DATABASE_EXTRA_SCHEMAS")
            folders += [directory / group / schema for schema in extras if (directory / group / schema).is_dir()]
        for folder in folders:
            for path in sorted(folder.glob("*.sql")):
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


def execute_sql(project, plan, *, first_is_dump=False, backup=False, skip=None):
    """Pre-open all inputs and serialize imports/fixtures with the same DB lock; returns skipped tables."""
    output = project.directory / ".generated"
    output.mkdir(mode=0o700, exist_ok=True)
    if first_is_dump:
        from project_storage import require_space
        size = plan[0].stat().st_size
        if plan[0].name.lower().endswith('.gz'):
            size = scan_dump(plan[0], main_schema(project), progress_meter())["size"]
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
                        unpack(path, handle, progress_meter())
                        handle.seek(0)
                    else:
                        handle = stack.enter_context(path.open("rb"))
                else:
                    handle = render_hook(project, path)  # Rendered before any change, so a bad hook stops early.
                inputs.append((path, handle))
            if backup:
                backup_database(project)
            skipped = set()
            for index, (path, handle) in enumerate(inputs):
                dump = index == 0 and first_is_dump
                print(f"{'Import' if dump else 'SQL'}: {path}", flush=True)
                # A failing dump or hook stops the sequence; no later SQL runs.
                if dump:
                    skipped = load_dump(project, handle, skip, progress_meter())
                else:
                    with project.database.load() as sink:
                        sink.write(handle)
            return skipped
