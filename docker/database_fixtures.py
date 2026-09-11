"""Select project-owned SQL fixture sets independently of the Docker environment."""

from pathlib import Path
import re

from database_import import execute_sql, render_hook


def plan_fixtures(project, selected):
    if not selected or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", selected):
        raise ValueError("Specify a fixture set with set=local or set=test (letters, digits, _ and -)")
    if not project.settings["DATABASE_NAME"]:
        raise ValueError("DATABASE_NAME is required for fixtures")
    configured = project.settings.get("FIXTURES_DIRECTORY", "")
    if not configured or Path(configured).is_absolute():
        raise ValueError("Set FIXTURES_DIRECTORY relative to the project root (for example app/database/fixtures)")
    directory = (project.root / configured).resolve()
    if not directory.is_relative_to(project.root) or not directory.is_dir():
        raise ValueError("FIXTURES_DIRECTORY must be a directory inside the project")
    # Reject links between sets as well as outside links: test must never silently
    # select local data, even when the target remains within the fixtures root.
    groups = ["common"] if selected == "common" else ["common", selected]
    plan = []
    for group in groups:
        folder = directory / group
        if folder.is_symlink():
            raise ValueError(f"Fixture group must not be a symlink: {group}")
        if not folder.exists() and group == "common" and selected != "common":
            continue
        if not folder.is_dir():
            raise ValueError(f"Fixture set directory does not exist: {folder}")
        for path in sorted(folder.glob("*.sql")):
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"Fixture must be a regular SQL file: {path}")
            render_hook(project, path)  # Read and validate every file before DB work.
            plan.append(path)
    return plan


def load_fixtures(project, plan):
    if not plan:
        print("No SQL fixtures in the selected set; database unchanged.")
        return
    execute_sql(project, plan)
    print("SQL fixtures completed.")
