#!/usr/bin/env python3
"""Prepare reusable Docker sources for one or more project roots, without starting Docker."""

import argparse
from pathlib import Path
import re
import sys


TEMPLATE = Path(__file__).resolve().parent / "templates/project"
SERVICES = ("mysql", "mariadb", "php", "apache", "nginx-proxy")


def planned_files(root, from_legacy=False, layout="root"):
    root = Path(root).resolve()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", root.name):
        raise ValueError(f"Project directory must have a lowercase project name: {root}")
    target = root / ("app/docker" if layout == "legacy" else "docker")
    legacy = root / "app/docker"
    if from_legacy and (layout == "legacy" or not (legacy / ".env").is_file()):
        raise ValueError(f"--from-legacy requires an existing {legacy / '.env'} and the root layout")
    files = {target / p.name: p.read_bytes() for p in TEMPLATE.iterdir() if p.is_file()}
    files[target / ".env"] = (legacy / ".env").read_bytes() if from_legacy else (
        f"# Public project identity; shared defaults live in setup/docker/.env\nPROJECT_NAME={root.name}\n"
    ).encode()
    if from_legacy:
        for name in (".env.local", ".env.stage", ".env.prod", "Dockerfile"):
            source = legacy / name
            if source.is_file():
                files[target / name] = source.read_bytes()
        if (legacy / "Dockerfile").is_file():
            raise ValueError("A legacy project Dockerfile needs its full build context reviewed before migration")
        for source in (legacy / "config").rglob("*"):
            if source.is_file():
                if source.is_symlink():
                    raise ValueError(f"Review the config symlink before migration: {source}")
                files[target / "config" / source.relative_to(legacy / "config")] = source.read_bytes()
        # Expanded legacy docker-compose.yml is never copied as a source. Real
        # environment-specific overlays need an explicit review of their semantics.
        overlays = list(legacy.glob("docker-compose.*.yml"))
        if overlays:
            raise ValueError("Review legacy Compose overlays before migration: " + ", ".join(map(str, overlays)))
    for service in SERVICES:
        files.setdefault(target / "config" / service / "local/.gitkeep", b"")
    # Existing settings and custom README/overrides belong to the project owner.
    # Existing incompatible bootstrap files are an error rather than a silent skip.
    for name in ("Makefile", "compose.yaml", ".gitignore"):
        output = target / name
        if output.is_symlink() or (output.exists() and output.read_bytes() != files[output]):
            raise ValueError(f"Existing {output} differs from the template; review before updating")
    return target, files


def prepare(root, from_legacy=False, layout="root", check=False):
    target, files = planned_files(root, from_legacy, layout)
    pending = [p for p in files if not p.exists()]
    if check:
        print(f"{target}: {len(pending)} files to create")
        return pending
    for output in pending:
        output.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation prevents a retry from replacing another writer's file.
        with output.open("xb") as handle:
            if output.name in (".env.local", ".env.stage", ".env.prod"):
                output.chmod(0o600)
            handle.write(files[output])
    print(f"Prepared {target}: {len(pending)} new files; existing project settings preserved")
    return pending


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("projects", nargs="+", type=Path, help="Project roots, e.g. ~/Projects/forsena")
    parser.add_argument("--from-legacy", action="store_true", help="Copy existing app/docker settings into docker")
    parser.add_argument("--layout", choices=("root", "legacy"), default="root")
    parser.add_argument("--check", action="store_true", help="Preview missing files without writing")
    args = parser.parse_args()
    try:
        # Preflight every project before starting a batch.
        for root in args.projects:
            planned_files(root, args.from_legacy, args.layout)
        for root in args.projects:
            prepare(root, args.from_legacy, args.layout, args.check)
    except (ValueError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
