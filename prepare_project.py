#!/usr/bin/env python3
"""Prepare reusable Docker sources for one or more project roots, without starting Docker."""

import argparse
from pathlib import Path
import re
import sys


TEMPLATE = Path(__file__).resolve().parent / "templates/project"
GROUPED_TEMPLATE = TEMPLATE.parent / "grouped"
MINIMAL_TEMPLATE = TEMPLATE.parent / "minimal"
SERVICES = ("mysql", "mariadb", "php", "apache", "nginx-proxy")
LAYOUT_DIRECTORIES = {"root": "docker", "app": "app", "legacy": "app/docker"}


def minimal_files(root, display_name=None):
    """Only the core sources used by create-project; extensions remain in setup."""
    root = Path(root).resolve()
    target = root / 'app'
    files = {target / path.relative_to(MINIMAL_TEMPLATE): path.read_bytes()
             for path in MINIMAL_TEMPLATE.rglob('*') if path.is_file()}
    files[target / 'Makefile'] = (TEMPLATE / 'Makefile').read_bytes()
    for path in files:
        files[path] = files[path].replace(b'__PROJECT_NAME__', root.name.encode())
        files[path] = files[path].replace(b'__PROJECT_DISPLAY_NAME__', (display_name or root.name).encode())
        files[path] = files[path].replace(b'__PROJECT_DOMAIN__', (root.name.replace('_', '-') + '.local').encode())
    return target, files


def project_layout(root, layout, from_legacy=False):
    if layout != "auto":
        return layout
    if from_legacy:
        return "root"
    # Prefer the consolidated sources over a retained legacy entry point.
    for candidate in ("app", "root", "legacy"):
        directory = root / LAYOUT_DIRECTORIES[candidate]
        if any((directory / marker).is_file() for marker in ("env/common.env", "compose/base.yaml", ".env", "compose.yaml")):
            return candidate
    return "root"


def source_layout(target, layout, sources):
    grouped = (target / "env/common.env").exists() or (target / "compose/base.yaml").exists()
    flat = (target / ".env").exists() or (target / "compose.yaml").exists()
    if grouped and flat:
        raise ValueError(f"Mixed flat and grouped sources in {target}; choose one layout first")
    detected = "grouped" if grouped else "flat" if flat else None
    if sources != "auto" and detected and sources != detected:
        raise ValueError(f"Existing {detected} sources in {target}; preparation does not convert source layouts")
    return detected if sources == "auto" and detected else (
        ("flat" if layout == "legacy" else "grouped") if sources == "auto" else sources)


def planned_files(root, from_legacy=False, layout="auto", sources="auto"):
    root = Path(root).resolve()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", root.name):
        raise ValueError(f"Project directory must have a lowercase project name: {root}")
    layout = project_layout(root, layout, from_legacy)
    target = root / LAYOUT_DIRECTORIES[layout]
    legacy = root / "app/docker"
    if from_legacy and (layout == "legacy" or not (legacy / ".env").is_file()):
        raise ValueError(f"--from-legacy requires an existing {legacy / '.env'} and the root layout")
    selected = source_layout(target, layout, sources)
    if selected == "grouped":
        files = {target / p.relative_to(GROUPED_TEMPLATE): p.read_bytes()
                 for p in GROUPED_TEMPLATE.rglob("*") if p.is_file()}
        files[target / "Makefile"] = (TEMPLATE / "Makefile").read_bytes()
        public = target / "env/common.env"
        files[public] = files[public].replace(b"__PROJECT_NAME__", root.name.encode()).replace(
            b"__DOCKER_RELATIVE__", target.relative_to(root).as_posix().encode())
        if from_legacy:
            files[public] += b"\n# Preserved legacy public settings\n" + (legacy / ".env").read_bytes()
    else:
        files = {target / p.name: p.read_bytes() for p in TEMPLATE.iterdir() if p.is_file()}
        files[target / ".env"] = (legacy / ".env").read_bytes() if from_legacy else (
            f"# Public project identity; shared defaults live in setup/docker/.env\nPROJECT_NAME={root.name}\n"
        ).encode()
    if from_legacy:
        for name in (".env.local", ".env.stage", ".env.prod", "Dockerfile"):
            source = legacy / name
            if source.is_file():
                destination = f"env/{name.removeprefix('.env.')}.env" if selected == "grouped" and name.startswith(".env.") else name
                files[target / destination] = source.read_bytes()
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
    bootstrap = ("Makefile",) if selected == "grouped" else ("Makefile", "compose.yaml", ".gitignore")
    for name in bootstrap:
        output = target / name
        if output.is_symlink() or (output.exists() and output.read_bytes() != files[output]):
            raise ValueError(f"Existing {output} differs from the template; review before updating")
    for output in files:
        if output.is_symlink() or not output.resolve().is_relative_to(target.resolve()):
            raise ValueError(f"Review source symlink before preparation: {output}")
        if output.exists() and not output.is_file():
            raise ValueError(f"Expected a file at {output}")
    return target, files


def prepare(root, from_legacy=False, layout="auto", check=False, sources="auto"):
    target, files = planned_files(root, from_legacy, layout, sources)
    pending = [p for p in files if not p.exists()]
    if check:
        print(f"{target}: {len(pending)} files to create")
        return pending
    for output in pending:
        output.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation prevents a retry from replacing another writer's file.
        with output.open("xb") as handle:
            if output.name in (".env.local", ".env.stage", ".env.prod", "local.env", "stage.env", "prod.env"):
                output.chmod(0o600)
            handle.write(files[output])
    print(f"Prepared {target}: {len(pending)} new files; existing project settings preserved")
    return pending


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("projects", nargs="+", type=Path, help="Project roots, e.g. ~/Projects/forsena")
    parser.add_argument("--from-legacy", action="store_true", help="Copy existing app/docker settings into docker")
    parser.add_argument("--layout", choices=("auto", "root", "app", "legacy"), default="auto",
                        help="Preserve existing location; app places sources beside app/public; new projects default to root")
    parser.add_argument("--sources", choices=("auto", "grouped", "flat"), default="auto",
                        help="Preserve existing layout; new root projects default to grouped sources")
    parser.add_argument("--check", action="store_true", help="Preview missing files without writing")
    args = parser.parse_args()
    try:
        # Preflight every project before starting a batch.
        for root in args.projects:
            planned_files(root, args.from_legacy, args.layout, args.sources)
        for root in args.projects:
            prepare(root, args.from_legacy, args.layout, args.check, args.sources)
    except (ValueError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
