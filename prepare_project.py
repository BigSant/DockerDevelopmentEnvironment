#!/usr/bin/env python3
"""Prepare reusable Docker sources for one or more project roots, without starting Docker."""

import argparse
from pathlib import Path
import re
import sys


TEMPLATES = Path(__file__).resolve().parent / "templates"
MAKEFILE = TEMPLATES / "shared/Makefile"
GROUPED_TEMPLATE = TEMPLATES / "grouped"
MINIMAL_TEMPLATE = TEMPLATES / "minimal"
LAYOUT_DIRECTORIES = {"root": "docker", "app": "app"}


def minimal_files(root):
    """Only the core sources used by create-project; extensions remain in setup."""
    root = Path(root).resolve()
    target = root / 'app'
    files = {target / path.relative_to(MINIMAL_TEMPLATE): path.read_bytes()
             for path in MINIMAL_TEMPLATE.rglob('*') if path.is_file()}
    files[target / 'Makefile'] = MAKEFILE.read_bytes()
    for path in files:
        files[path] = files[path].replace(b'__PROJECT_NAME__', root.name.encode())
        files[path] = files[path].replace(b'__PROJECT_DOMAIN__', (root.name.replace('_', '-') + '.local').encode())
    return target, files


def project_layout(root, layout):
    if layout != "auto":
        return layout
    for candidate in ("app", "root"):
        directory = root / LAYOUT_DIRECTORIES[candidate]
        if any((directory / marker).is_file() for marker in ("env/common.env", "compose/base.yaml")):
            return candidate
    return "app"


def planned_files(root, layout="auto"):
    root = Path(root).resolve()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", root.name):
        raise ValueError(f"Project directory must have a lowercase project name: {root}")
    layout = project_layout(root, layout)
    target = root / LAYOUT_DIRECTORIES[layout]
    if (target / ".env").exists() or (target / "compose.yaml").exists():
        raise ValueError(f"Root-level .env / compose.yaml sources in {target} are no longer supported; use env/ + compose/")
    files = {target / p.relative_to(GROUPED_TEMPLATE): p.read_bytes()
             for p in GROUPED_TEMPLATE.rglob("*") if p.is_file()}
    files[target / "Makefile"] = MAKEFILE.read_bytes()
    public = target / "env/common.env"
    files[public] = files[public].replace(b"__PROJECT_NAME__", root.name.encode()).replace(
        b"__DOCKER_RELATIVE__", target.relative_to(root).as_posix().encode())
    # Existing settings and custom README/overrides belong to the project owner.
    # An existing incompatible Makefile is an error rather than a silent skip.
    output = target / "Makefile"
    if output.is_symlink() or (output.exists() and output.read_bytes() != files[output]):
        raise ValueError(f"Existing {output} differs from the template; review before updating")
    for output in files:
        if output.is_symlink() or not output.resolve().is_relative_to(target.resolve()):
            raise ValueError(f"Review source symlink before preparation: {output}")
        if output.exists() and not output.is_file():
            raise ValueError(f"Expected a file at {output}")
    return target, files


def prepare(root, layout="auto", check=False):
    target, files = planned_files(root, layout)
    pending = [p for p in files if not p.exists()]
    if check:
        print(f"{target}: {len(pending)} files to create")
        return pending
    for output in pending:
        output.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation prevents a retry from replacing another writer's file.
        with output.open("xb") as handle:
            if output.name in ("local.env", "stage.env", "prod.env"):
                output.chmod(0o600)
            handle.write(files[output])
    print(f"Prepared {target}: {len(pending)} new files; existing project settings preserved")
    return pending


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("projects", nargs="+", type=Path, help="Project roots, e.g. ~/Projects/forsena")
    parser.add_argument("--layout", choices=("auto", "app", "root"), default="auto",
                        help="Preserve an existing location; new projects keep sources beside app/public")
    parser.add_argument("--check", action="store_true", help="Preview missing files without writing")
    args = parser.parse_args()
    try:
        # Preflight every project before starting a batch.
        for root in args.projects:
            planned_files(root, args.layout)
        for root in args.projects:
            prepare(root, args.layout, args.check)
    except (ValueError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
