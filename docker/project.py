#!/usr/bin/env python3
"""Run the shared Compose sources for one project; rendered files are output only."""

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile

from database_import import import_database, plan_import
from database_fixtures import load_fixtures, plan_fixtures
from database_schema import check_schema, export_schema, install_schema_hook
from project_environment import doctor, initialize_directories, initialize_env, pull_images
from project_ide import initialize_ide, refresh_ide


DOCKER_ROOT = Path(__file__).resolve().parent
ENV_KEY = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*[=:]", re.M)


def project_root(directory):
    return directory.parent.parent if directory.parent.name == "app" else directory.parent


class Project:
    def __init__(self, directory, environment="local", root=None, profiles=None):
        self.directory = Path(directory).resolve()
        self.root = Path(root).resolve() if root else project_root(self.directory)
        self.environment = environment
        grouped = (self.directory / "env/common.env").exists() or (self.directory / "compose/base.yaml").exists()
        if grouped:
            if (self.directory / ".env").exists() or (self.directory / "compose.yaml").exists():
                raise ValueError("Use one source layout: env/ + compose/, or the root-level files")
            common_env = self.directory / "env/common.env"
            private_env = self.directory / f"env/{environment}.env"
            source = self.directory / "compose/base.yaml"
            overrides = [self.directory / "compose/common.yaml", self.directory / f"compose/{environment}.yaml"]
            if not source.is_file():
                raise ValueError(f"Missing {source}")
        else:
            common_env = self.directory / ".env"
            private_env = self.directory / f".env.{environment}"
            source = self.directory / "compose.yaml"
            source = source if source.is_file() else DOCKER_ROOT / "docker-compose.yml"
            overrides = [self.directory / "compose.override.yaml", self.directory / f"compose.{environment}.override.yaml"]
        if not common_env.is_file():
            raise ValueError(f"Missing {common_env}; run prepare_project.py first")
        if not private_env.is_file():
            raise ValueError(f"Create {private_env} from its example")
        self.env_files = [DOCKER_ROOT / ".env", common_env, private_env]
        # Do not let a previous project's shell exports silently select this stack.
        self.process_env = os.environ.copy()
        for env_file in self.env_files:
            for key in ENV_KEY.findall(env_file.read_text()):
                self.process_env.pop(key, None)
        for key in ("COMPOSE_PROJECT_NAME", "COMPOSE_FILE", "COMPOSE_PROFILES",
                    "COMPOSE_ENV_FILES", "ENV_FILE"):
            self.process_env.pop(key, None)
        self.process_env.update({
            "ROOT_DIRECTORY": str(DOCKER_ROOT),
            "PROJECT_DIRECTORY": str(self.root),
            "PROJECT_APP_DIRECTORY": str(self.root / "app"),
            "PROJECT_WEB_DIRECTORY": str(self.root / "app/public"),
            "PROJECT_CONFIG_DIRECTORY": str(self.root / "app/config"),
            "PROJECT_DATA_DIRECTORY": str(self.root / "data"),
            "PROJECT_DOCKER_DIRECTORY": str(self.directory),
            "DOCKERFILE_DIRECTORY": str(self.directory if (self.directory / "Dockerfile").is_file()
                                        else DOCKER_ROOT),
            "ENV": environment, "HOST_UID": str(os.getuid()), "HOST_GID": str(os.getgid()),
            "COMPOSE_DISABLE_ENV_FILE": "true",
            "BUILDX_NO_DEFAULT_ATTESTATIONS": "1", "BUILDX_METADATA_PROVENANCE": "disabled",
        })
        self.command = ["docker", "compose", "--project-directory", str(self.directory)]
        for env_file in self.env_files:
            self.command += ["--env-file", str(env_file)]
        base_command = self.command.copy()
        # JSON preserves multiline/special dotenv values without reparsing text.
        self.command += ["--project-name", "setup-settings", "-f", str(DOCKER_ROOT / "project-settings.yaml")]
        settings = json.loads(self.capture(["config", "--format", "json"]))["services"]["settings"]["environment"]
        self.settings = settings
        name = settings.get("PROJECT_NAME", "")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
            raise ValueError(f"Set a valid lowercase PROJECT_NAME in {common_env}")
        self.name = f"{name}-{environment}"
        self.profiles = profiles if profiles is not None else (
            settings["PROFILES"] if settings["PROFILES_DEFINED"] else settings[f"{environment.upper()}_PROFILES"])
        self.command = base_command + ["--project-name", self.name]
        self.command += ["-f", str(source)]
        extras = []
        for entry in shlex.split(settings["EXTRA_COMPOSE_FILES"]):
            extra = (self.directory / entry).resolve()
            if not extra.is_relative_to(self.directory) or not extra.is_file():
                raise ValueError(f"PROJECT_COMPOSE_FILES must reference existing files inside {self.directory}")
            extras.append(extra)
        # Explicit component files (e.g. Redis) load before environment overrides.
        for override in [overrides[0], *extras, overrides[1]]:
            if override.is_file():
                self.command += ["-f", str(override)]

    def child_env(self, profiles=None):
        selected = getattr(self, "profiles", None) if profiles is None else profiles
        return {**self.process_env, **({"COMPOSE_PROFILES": selected} if selected is not None else {})}

    def capture(self, args, profiles=None):
        result = subprocess.run(self.command + args, env=self.child_env(profiles),
                                capture_output=True, text=True)
        if result.returncode:
            # Compose errors can include interpolated credentials; keep diagnostics private.
            raise ValueError(f"Compose {args[0]} failed (exit {result.returncode}); check the source YAML and env files")
        return result.stdout

    def run(self, args, profiles=None):
        subprocess.run(self.command + args, env=self.child_env(profiles), check=True)

    def model(self):
        return json.loads(self.capture(["config", "--format", "json"], profiles="*"))

    def render(self):
        contents = self.capture(["config"], profiles="*")
        output_dir = self.directory / ".generated"
        output_dir.mkdir(mode=0o700, exist_ok=True)
        target = output_dir / f"compose.{self.environment}.yaml"
        # Each invocation owns its temporary file, even for the same project.
        with tempfile.NamedTemporaryFile(mode="w", dir=output_dir, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(contents)
        try:
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker-directory", required=True)
    parser.add_argument("--project-directory")
    parser.add_argument("--env", choices=["local", "stage", "prod"], default="local")
    parser.add_argument("--profiles", help="Explicit profiles; an empty value selects core services")
    parser.add_argument("action", choices=["check", "config", "up", "build", "down", "ps", "logs",
                                           "phpstan", "phpstan-baseline", "phpcs", "e2e", "doctrine",
                                           "db-import", "db-import-plan", "db-fixtures-load", "db-fixtures-plan", "schema-export", "schema-check",
                                           "schema-hook-install", "init", "doctor", "pull", "shell", "ide-init", "ide-refresh"])
    parser.add_argument("--docker-server", help="Existing PhpStorm Docker connection name for ide-init")
    parser.add_argument("--ide-config-directory", type=Path, help="PhpStorm configuration directory for Docker connection discovery")
    parser.add_argument("--command", help="QA command override, parsed as arguments (no shell)")
    parser.add_argument("--dump", help="Plain .sql dump for db-import / db-import-plan")
    parser.add_argument("--db-fixtures", help="Fixture set for db-fixtures-* or optional SQL after db-import hooks")
    args = parser.parse_args()
    try:
        if args.action in ("init", "ide-init"):
            initialize_env(args.docker_directory, args.env)
        project = Project(args.docker_directory, args.env, args.project_directory, args.profiles)
        if args.action == "ide-init":
            initialize_ide(project, args.docker_server, args.ide_config_directory)
        elif args.action == "ide-refresh":
            refresh_ide(project)
        elif args.action == "init":
            initialize_directories(project)
        elif args.action == "doctor":
            doctor(project)
        elif args.action == "pull":
            pull_images(project)
        elif args.action == "shell":
            project.run(["exec", "php-fpm", "sh"])
        elif args.action == "check":
            project.capture(["config", "--quiet"], profiles="*")
            print(f"Valid: {project.name}; sources: {project.directory}")
        elif args.action == "config":
            print(project.render())
        elif args.action == "build":
            project.run(["build", "php-fpm-base"], profiles="build_only")
            project.run(["build"])
        elif args.action == "up":
            project.run(["up", "-d", "--no-build", "--pull", "never"])
        elif args.action in ("down", "ps", "logs"):
            project.run([args.action])
        elif args.action in ("schema-export", "schema-check", "schema-hook-install"):
            {"schema-export": export_schema, "schema-check": check_schema,
             "schema-hook-install": install_schema_hook}[args.action](project)
        elif args.action in ("db-import", "db-import-plan"):
            plan = plan_import(project, args.dump)
            if args.db_fixtures is not None:
                plan += plan_fixtures(project, args.db_fixtures)
            if args.action == "db-import-plan":
                print(f"Database: {project.settings['DATABASE_NAME']}; environment: {project.environment}")
                for step in plan:
                    print(step)
            else:
                import_database(project, plan)
        elif args.action in ("db-fixtures-load", "db-fixtures-plan"):
            plan = plan_fixtures(project, args.db_fixtures)
            print(f"Database: {project.settings['DATABASE_NAME']}; environment: {project.environment}; fixture set: {args.db_fixtures}")
            if args.action == "db-fixtures-plan":
                for step in plan:
                    print(step)
                if not plan:
                    print("No SQL fixtures in the selected set.")
            else:
                load_fixtures(project, plan)
        elif args.action == "phpstan-baseline":
            baseline = project.settings["PHPSTAN_BASELINE_FILE"]
            if not baseline or not baseline.startswith("/"):
                raise ValueError("Set PHPSTAN_BASELINE_FILE to the mounted baseline file's container path")
            project.run(["run", "--rm", "--no-deps", "--entrypoint", "phpstan", "php-phpstan",
                         "analyse", "--configuration=/tmp/phpstan/config/phpstan.neon",
                         f"--generate-baseline={baseline}", "--allow-empty-baseline"], profiles="phpstan")
        elif args.action == "doctrine":
            arguments = shlex.split(args.command or "status")
            project.run(["run", "--rm", "--no-deps", "php-doctrine-migrations"] + arguments, profiles="doctrine")
        else:
            service, profile, entrypoint, default = {
                "phpstan": ("php-phpstan", "phpstan", "make", "report"),
                "phpcs": ("php-cs", "phpcs", "make", "check"),
                "e2e": ("playwright", "playwright", "", project.settings["PLAYWRIGHT_COMMAND"]),
            }[args.action]
            command = ["run", "--rm"]
            arguments = shlex.split(args.command or default)
            if not arguments or not arguments[0]:
                raise ValueError("QA command cannot be empty")
            if args.action != "e2e":
                command += ["--no-deps", "--entrypoint", entrypoint]
            else:
                # The shared Playwright service otherwise starts `tail -f /dev/null`.
                command += ["--entrypoint", arguments.pop(0)]
            project.run(command + [service] + arguments, profiles=profile)
        return 0
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
