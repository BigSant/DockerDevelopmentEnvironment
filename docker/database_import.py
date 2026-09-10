"""Stream an explicit SQL dump, then ordered project hooks, to its Compose DB."""

from contextlib import ExitStack
import fcntl
import os
from pathlib import Path
import subprocess


MYSQL_IMPORT = '''
set -eu
if [ "$MYSQL_DATABASE" != "$1" ]; then
    echo 'Configured database differs from the running container; reconcile it before importing.' >&2
    exit 64
fi
MYSQL_PWD="$MYSQL_PASSWORD" exec mysql --binary-mode --get-server-public-key --user="$MYSQL_USER" --database="$1"
'''


def plan_import(project, dump):
    if not dump:
        raise ValueError("Specify a dump with file=/path/to/dump.sql")
    source = Path(dump).resolve()
    if source.suffix.lower() != ".sql" or not source.is_file() or source.stat().st_size == 0:
        raise ValueError("The dump must be a readable, nonempty, plain .sql file")
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
    return plan


def import_database(project, plan):
    output = project.directory / ".generated"
    output.mkdir(mode=0o700, exist_ok=True)
    lock_path = output / f"db-import.{project.environment}.lock"
    with os.fdopen(os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600), "r+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("An import for this project/environment is already running") from error
        with ExitStack() as stack:
            inputs = [(path, stack.enter_context(path.open("rb"))) for path in plan]
            command = project.command + ["exec", "-T", "database", "sh", "-c", MYSQL_IMPORT,
                                         "database-import", project.settings["DATABASE_NAME"]]
            for index, (path, handle) in enumerate(inputs):
                print(f"{'Import' if index == 0 else 'After import'}: {path.name}", flush=True)
                # A failing dump or hook stops the sequence; no later SQL runs.
                subprocess.run(command, env=project.child_env(), stdin=handle, check=True)
    print("Dump and after-import SQL completed.")
