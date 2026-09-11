"""Stream an explicit SQL dump, then ordered project hooks, to its Compose DB."""

from contextlib import ExitStack
import fcntl
import os
from pathlib import Path
import re
import subprocess
import tempfile


from database_client import IMPORT as MYSQL_IMPORT
from database_backup import backup_database
import gzip
import shutil


def render_hook(project, path):
    contents = path.read_bytes()
    if b"${DOMAIN}" in contents:
        domain = project.settings.get("SQL_DOMAIN") or project.settings.get("DOMAIN", "")
        # Only a domain/optional port is accepted, never shell or SQL syntax.
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?(?::[0-9]{1,5})?", domain):
            raise ValueError("SQL hooks using ${DOMAIN} require a valid DOMAIN hostname")
        contents = contents.replace(b"${DOMAIN}", domain.encode("ascii"))
    return contents


def plan_import(project, dump):
    if not dump:
        raise ValueError("Specify a dump with file=/path/to/dump.sql")
    source = Path(dump).resolve()
    if not (source.name.lower().endswith(".sql") or source.name.lower().endswith(".sql.gz")) or not source.is_file() or source.stat().st_size == 0:
        raise ValueError("The dump must be a readable, nonempty .sql or .sql.gz file")
    if not project.settings["DATABASE_NAME"]:
        raise ValueError("DATABASE_NAME is required for import")
    configured = project.settings["POST_IMPORT_SQL_DIRECTORY"]
    if not configured:
        raise ValueError("Set POST_IMPORT_SQL_DIRECTORY to the project's after-import SQL directory")
    directory = (project.root / configured).resolve()
    if not directory.is_relative_to(project.root) or not directory.is_dir():
        raise ValueError("POST_IMPORT_SQL_DIRECTORY must be a directory inside the project")
    hooks = []
    for group in ("common", project.environment):
        for path in sorted((directory / group).glob("*.sql")):
            resolved = path.resolve()
            if not resolved.is_relative_to(directory) or not resolved.is_file():
                raise ValueError(f"SQL hook must be a file inside {directory}: {path.name}")
            hooks.append(resolved)
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


def import_database(project, plan, backup=False):
    execute_sql(project, plan, first_is_dump=True, backup=backup)
    if project.settings.get('PROFILE') in ('ps', 'prestashop'):
        running = project.capture(['ps', '--status', 'running', '--services']).split()
        if 'php-fpm' in running:
            # A restored dump may bring back cache/mail settings from production.
            # If PHP is stopped, its next startup performs this preparation instead.
            project.capture(['exec', '-T', 'php-fpm', 'php', '/opt/setup/runtime/prestashop-command.php', 'policy'])
            project.capture(['exec', '-T', 'php-fpm', 'php', '/opt/setup/runtime/prestashop-command.php', 'cache-clear'])
            project.run(['restart', 'php-fpm'])
    print("Dump and after-import SQL completed.")


def execute_sql(project, plan, *, first_is_dump=False, backup=False):
    """Pre-open all inputs and serialize imports/fixtures with the same DB lock."""
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
            for index, (path, handle) in enumerate(inputs):
                print(f"{'Import' if index == 0 and first_is_dump else 'SQL'}: {path}", flush=True)
                # A failing dump or hook stops the sequence; no later SQL runs.
                subprocess.run(command, env=project.child_env(), stdin=handle, check=True)
