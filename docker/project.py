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
from project_health import smoke, start_project
from database_backup import backup_database
from setup_release import API_VERSION, image_suffix, setup_info
from project_policy import DEFAULTS, resolve_policy, validate_configuration


DOCKER_ROOT = Path(__file__).resolve().parent
ENV_KEY = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*[=:]", re.M)


def project_root(directory):
    return directory.parent.parent if directory.parent.name == "app" else directory.parent


class Project:
    def __init__(self, directory, environment="local", root=None, profiles=None):
        self.directory = Path(directory).resolve()
        self.root = Path(root).resolve() if root else project_root(self.directory)
        self.environment = environment
        build_environment = 'local' if environment == 'test' else environment
        self.web_directory = self.root / 'app/public'
        self.data_directory = self.root / 'data'
        if environment == 'test':
            self.web_directory = self.directory / '.generated/test/app'
            self.data_directory = self.directory / '.generated/test/data'
        elif environment != 'local':
            self.web_directory = self.root / 'app' / environment / 'public'
            self.data_directory = self.root / 'data' / environment
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
        for key in DEFAULTS:
            self.process_env.pop(key, None)
        self.process_env.update({
            "ROOT_DIRECTORY": str(DOCKER_ROOT),
            "PROJECT_DIRECTORY": str(self.root),
            "PROJECT_APP_DIRECTORY": str(self.root / "app"),
            "PROJECT_WEB_DIRECTORY": str(self.web_directory),
            "PROJECT_CONFIG_DIRECTORY": str(self.root / "app/config"),
            "PROJECT_DATA_DIRECTORY": str(self.data_directory),
            "PROJECT_DOCKER_DIRECTORY": str(self.directory),
            "DOCKERFILE_DIRECTORY": str(self.directory if (self.directory / "Dockerfile").is_file()
                                        else DOCKER_ROOT),
            "BUILD_ENV": build_environment, "IMAGE_ENV": build_environment,
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
        if str(settings['SETUP_REQUIRED_API']) != API_VERSION:
            raise ValueError('Project requires a different setup API; use a compatible setup release')
        profile = settings['PROFILE']
        if profile == 'ps': profile = 'prestashop'
        settings['PROFILE'] = profile
        self.process_env['PROFILE'] = profile
        policy = resolve_policy(settings, environment)
        settings.update(policy)
        self.process_env.update(policy)
        self.process_env['SETUP_IMAGE_SUFFIX'] = ''
        for key, attribute in [('DATA_DIRECTORY', 'data_directory'), ('APP_SOURCE_DIRECTORY', 'web_directory')]:
            if settings[key]:
                path = (self.root / settings[key]).resolve()
                if not path.is_relative_to(self.root):
                    raise ValueError(f'{key} must stay inside the project')
                if environment == 'test' and not path.is_relative_to(self.directory / '.generated/test'):
                    raise ValueError('Test data and code must stay inside .generated/test')
                setattr(self, attribute, path)
        self.process_env.update(PROJECT_DATA_DIRECTORY=str(self.data_directory), PROJECT_WEB_DIRECTORY=str(self.web_directory))
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
        active = json.loads(self.capture(['config', '--format', 'json']))['services']
        self.process_env.update(SETUP_ENABLE_PMA='1' if 'pma' in active else '0',
                                SETUP_ENABLE_MAILPIT='1' if 'mailpit' in active else '0')
        if settings['VERSIONED_IMAGES'] == '1':
            arguments = {name: service['build'].get('args', {})
                         for name, service in self.model()['services'].items() if service.get('build')}
            self.process_env['SETUP_IMAGE_SUFFIX'] = image_suffix(profile, arguments)
        if environment == 'test':
            self.validate_test_isolation()

    def validate_test_isolation(self, profiles=None):
        from project_isolation import validate_test_model
        model = json.loads(self.capture(['config', '--format', 'json'], profiles=profiles))
        validate_test_model(self, model)

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
        if self.environment == 'test' and profiles is not None:
            self.validate_test_isolation(profiles)
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
    parser.add_argument("--env", choices=["local", "stage", "prod", "test"], default="local")
    parser.add_argument("--profiles", help="Explicit profiles; an empty value selects core services")
    parser.add_argument("action", choices=["check", "config", "up", "build", "down", "ps", "logs",
                                           "phpstan", "phpstan-baseline", "phpcs", "e2e", "doctrine",
                                           "restart", "composer", "cache-clear", "db-backup-prune", "runtime-info",
                                           "db-import", "db-import-plan", "db-fixtures-load", "db-fixtures-plan", "schema-export", "schema-check",
                                           "schema-hook-install", "init", "doctor", "pull", "shell", "ide-init", "ide-refresh", "smoke", "db-backup", "db-prepare", "bootstrap", "test-init", "setup-info"])
    parser.add_argument("--docker-server", help="Existing PhpStorm Docker connection name for ide-init")
    parser.add_argument("--ide-config-directory", type=Path, help="PhpStorm configuration directory for Docker connection discovery")
    parser.add_argument("--command", help="QA command override, parsed as arguments (no shell)")
    parser.add_argument("--dump", help=".sql or .sql.gz dump for db-import / db-import-plan")
    parser.add_argument("--db-fixtures", help="Fixture set for db-fixtures-* or optional SQL after db-import hooks")
    parser.add_argument('--backup', action='store_true', help='Create a private backup before importing')
    parser.add_argument('--refresh-test', action='store_true', help='Stop and refresh the isolated test application checkout')
    parser.add_argument('--output', help='New backup destination (.sql or .sql.gz)')
    parser.add_argument('--timeout', type=int, default=90, help='Readiness/HTTP timeout in seconds')
    parser.add_argument('--service', help='One enabled service for logs/restart')
    parser.add_argument('--follow', action='store_true')
    parser.add_argument('--tail', type=int, default=100)
    parser.add_argument('--apply', action='store_true', help='Apply the backup pruning plan')
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error('--timeout must be positive')
    if args.tail < 0: parser.error('--tail must not be negative')
    if args.action == 'setup-info':
        setup_info()
        return 0
    try:
        if args.action in ("init", "ide-init", "bootstrap"):
            initialize_env(args.docker_directory, args.env)
        project = Project(args.docker_directory, args.env, args.project_directory, args.profiles)
        if args.action == 'runtime-info':
            from project_policy import DEFAULTS
            print(f'Environment: {project.environment}; project: {project.name}; profile: {project.settings["PROFILE"] or "generic"}')
            for key in DEFAULTS:
                print(f'{key}={project.settings[key]}')
        elif args.action == 'db-backup-prune':
            from project_storage import prune_backups
            prune_backups(project, args.apply)
        elif args.action in ('restart', 'logs', 'composer', 'cache-clear'):
            from project_commands import restart, logs, composer, cache_clear
            if args.action == 'restart': restart(project, args.service, args.timeout)
            elif args.action == 'logs': logs(project, args.service, args.follow, args.tail)
            elif args.action == 'composer': composer(project, args.command)
            else: cache_clear(project)
        elif args.action == 'test-init':
            from project_bootstrap import initialize_test
            initialize_test(project, refresh=args.refresh_test)
        elif args.action == 'bootstrap':
            from project_bootstrap import bootstrap
            project = bootstrap(project)
        elif args.action == 'db-prepare':
            project.run(['up', '-d', '--no-deps', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', str(args.timeout), 'database'])
        elif args.action == 'db-backup':
            backup_database(project, args.output)
        elif args.action == 'smoke':
            smoke(project, args.timeout)
        elif args.action == "ide-init":
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
            validate_configuration(project)
            print(f"Valid: {project.name}; sources: {project.directory}")
        elif args.action == "config":
            print(project.render())
        elif args.action == "build":
            project.run(["build", "php-fpm-base"], profiles="build_only")
            project.run(["build"])
        elif args.action == "up":
            start_project(project, args.timeout)
        elif args.action in ("down", "ps"):
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
                import_database(project, plan, backup=args.backup)
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
        if args.action not in ('ide-init', 'ide-refresh', 'test-init', 'setup-info') and (project.directory / '.idea/php.xml').is_file():
            refresh_ide(project)
        return 0
    except (ValueError, OSError, EOFError, subprocess.CalledProcessError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
