"""Read MySQL table DDL and compare it with the exact Git index being committed."""

import json
import hashlib
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
from urllib.parse import quote


MANIFEST = "schema.manifest.json"
HOOK_MARKER = "# Shared setup schema pre-commit hook v1"
from database_client import READ as MYSQL_READ
TABLES_SQL = "SELECT HEX(TABLE_NAME) FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE' ORDER BY BINARY TABLE_NAME;\n"


def mysql_query(project, sql):
    database = project.settings["DATABASE_NAME"]
    if not database:
        raise ValueError("DATABASE_NAME is required for schema commands")
    command = project.command + ["exec", "-T", "database", "sh", "-c", MYSQL_READ,
                                 "schema-read", database]
    try:
        result = subprocess.run(command, input=sql.encode(), capture_output=True,
                                env=project.child_env(), timeout=60)
    except subprocess.TimeoutExpired as error:
        raise ValueError("Schema read timed out; no successful check or export was made") from error
    if result.returncode:
        # Do not print Compose diagnostics, which may contain interpolated secrets.
        raise ValueError("Cannot read DB schema; check the running database service, name and credentials")
    return result.stdout.decode("utf-8")


def unescape_batch(value):
    # MySQL batch output escapes backslashes before control characters.
    escapes = {"0": "\0", "t": "\t", "n": "\n", "r": "\r", "b": "\b", "\\": "\\"}
    return re.sub(r"\\([0tnrb\\])", lambda match: escapes[match[1]], value)


def table_file(name):
    encoded = quote(name, safe="")
    if len(encoded) > 200:
        encoded = encoded[:150] + "-" + hashlib.sha256(name.encode()).hexdigest()[:24]
    return "table-" + encoded + ".sql"


def normalize_ddl(ddl):
    # Only the table option after ENGINE; retain column AUTO_INCREMENT and comments/defaults.
    ddl = re.sub(r"(?m)^(\) ENGINE=\S+) AUTO_INCREMENT=\d+\b", r"\1", ddl)
    return ddl.rstrip() + ";\n"


def read_schema(project):
    names = [bytes.fromhex(line).decode("utf-8") for line in mysql_query(project, TABLES_SQL).splitlines()]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate table names in schema response")
    definitions = {}
    if names:
        statements = "SET SESSION sql_quote_show_create=1; SET SESSION sql_mode='';\n"
        statements += "\n".join("SHOW CREATE TABLE `" + name.replace("`", "``") + "`;" for name in names)
        for line in mysql_query(project, statements).split("\n"):
            if not line:
                continue
            fields = line.split("\t")
            if len(fields) != 2:
                raise ValueError("Unexpected SHOW CREATE TABLE response")
            name, ddl = map(unescape_batch, fields)
            if name not in names or name in definitions or not ddl.startswith("CREATE TABLE "):
                raise ValueError("Incomplete or unexpected table definition")
            definitions[name] = normalize_ddl(ddl)
    if set(definitions) != set(names):
        raise ValueError("Schema changed during collection; retry without concurrent DDL")
    # Catch table additions/removals during collection; this does not lock out concurrent ALTER.
    after = [bytes.fromhex(line).decode("utf-8") for line in mysql_query(project, TABLES_SQL).splitlines()]
    if after != names:
        raise ValueError("Table list changed during collection; retry without concurrent DDL")
    files = {table_file(name): definitions[name].encode() for name in sorted(names)}
    manifest = {"format": 1, "dialect": "mysql", "tables": [
        {"name": name, "file": table_file(name)} for name in sorted(names)]}
    files[MANIFEST] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    return files


def schema_directory(project):
    configured = project.settings.get("SCHEMA_DIRECTORY", "")
    if not configured:
        raise ValueError("Set SCHEMA_DIRECTORY relative to the project root (for example database/schema)")
    directory = (project.root / configured).resolve()
    if directory == project.root or not directory.is_relative_to(project.root):
        raise ValueError("SCHEMA_DIRECTORY must be a subdirectory inside the project")
    return directory


def managed_file(name):
    return name == MANIFEST or (name.startswith("table-") and name.endswith(".sql"))


def export_schema(project):
    directory = schema_directory(project)
    files = read_schema(project)  # Finish all DB reads before changing any files.
    directory.mkdir(parents=True, exist_ok=True)
    previous = set()
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
                    or table.get("file") != table_file(table["name"])):
                raise ValueError("Invalid table filename in schema manifest")
            previous.add(table["file"])
    for path in directory.iterdir():
        if managed_file(path.name):
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"Refusing to replace non-regular schema file: {path.name}")
            if path.name != MANIFEST and path.name not in previous:
                raise ValueError(f"Unmanaged schema file: {path.name}; review it before export")
    # Replace each file atomically; unchanged exports preserve mtimes.
    for name, contents in files.items():
        target = directory / name
        if target.exists() and target.read_bytes() == contents:
            continue
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(contents)
        try:
            temporary.chmod(0o644)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    for name in previous - files.keys():
        (directory / name).unlink(missing_ok=True)
    print(f"Exported {len(files) - 1} tables to {directory}. Review, then git add -A that directory.")


def git_env():
    # Hooks may export relative index/Git paths; preserve their meaning across git -C.
    env = os.environ.copy()
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        if env.get(key):
            env[key] = str(Path(env[key]).absolute())
    return env


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


def check_schema(project):
    directory = schema_directory(project)
    staged = staged_schema(directory)
    actual = read_schema(project)
    differences = []
    for name in sorted(actual.keys() | staged.keys()):
        if name not in staged:
            differences.append(f"not staged: {name}")
        elif name not in actual:
            differences.append(f"no longer in DB: {name}")
        elif staged[name] != actual[name]:
            differences.append(f"different: {name}")
    if differences:
        raise ValueError("DB schema differs from the Git index:\n  " + "\n  ".join(differences)
                         + "\nRun make schema-export, review the changes, then git add -A the schema directory.")
    print(f"Schema matches the Git index ({len(actual) - 1} tables).")


def install_schema_hook(project):
    directory = schema_directory(project)
    root, _ = schema_repository(directory)
    custom = subprocess.run(["git", "-C", str(root), "config", "--get", "core.hooksPath"], capture_output=True, env=git_env())
    if custom.returncode != 1:
        raise ValueError("core.hooksPath is configured or unreadable; add schema-check to the existing hook manager")
    hook = Path(os.fsdecode(git(root, "rev-parse", "--path-format=absolute", "--git-path", "hooks/pre-commit")).strip())
    command = [sys.executable, str(Path(__file__).with_name("project.py")),
               "--docker-directory", str(project.directory), "--project-directory", str(project.root),
               "--env", project.environment, "schema-check"]
    contents = "#!/bin/sh\n" + HOOK_MARKER + "\nexec " + shlex.join(command) + "\n"
    if hook.is_symlink() or (hook.exists() and hook.read_text() != contents):
        raise ValueError(f"Existing hook preserved: {hook}; add schema-check to it manually")
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(contents)
    hook.chmod(0o755)
    print(f"Installed schema pre-commit hook in {root} for {project.environment}.")
