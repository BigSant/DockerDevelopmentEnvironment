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

from database_import import check_dump_tables, import_database, plan_import
from database_fixtures import load_fixtures, plan_fixtures
from database_schema import check_schema, export_schema, install_schema_hook
from project_environment import doctor, initialize_directories, initialize_env, pull_images
from project_ide import initialize_ide, refresh_ide
from project_health import start_project
from project_metrics import check_token, resolve_metrics
from database_backup import backup_database
from database_client import extra_schemas
from project_prestashop import KEY_NAMES as PRESTASHOP_KEYS
from setup_release import image_suffixes, setup_info, suffix_variable
from project_policy import DEFAULTS, LEGACY_RESOURCE_KEYS, MissingLimits, resolve_policy, suggested_limits, validate_configuration


DOCKER_ROOT = Path(__file__).resolve().parent
# Optional CORE services a project enables with SERVICES=...; the four core services always run.
SERVICE_CATALOGUE = ('pma', 'mailpit', 'cron', 'dbmonitor', 'backup', 'metrics', 'memcached', 'redis', 'playwright', 'php-phpstan', 'php-cs',
                     'php-doctrine-migrations')
# Long-running catalogue services start with `up`; the others are tools started by their command.
RUNTIME_PROFILES = {'cron': 'cron', 'backup': 'backup'}
# Web tools that stage/prod start only behind a client IP allowlist.
ALLOWLISTED_TOOLS = ('pma', 'mailpit', 'cron', 'dbmonitor', 'backup', 'metrics')
# With DEPLOY_CONTEXT, stage/prod containers run on the target: exec/ps/logs go there, builds
# and model rendering stay on this build machine, and lifecycle commands use the release model.
LOCAL_COMPOSE_COMMANDS = ('build', 'config', 'images', 'pull')
RELEASE_COMPOSE_COMMANDS = ('up', 'down', 'run', 'create', 'start', 'rm')
LEGACY_CORE_INCLUDE = re.compile(r"ROOT_DIRECTORY\}?/docker/(nginx-proxy|apache|mysql|mariadb|php-fpm)/docker-compose\.yml")
ENV_ENTRY = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*[=:][ \t]*(.*)$", re.M)
# Secrets a pipeline passes as process environment (Bitbucket Deployment variables, GitHub environment
# secrets). An env file that leaves one blank does not hide the process value; any other key named in an
# env file does.
# Basic-auth users of the browser tools (nginx-proxy access.sh), optional on top of their IP allowlist.
TOOL_PASSWORDS = {tool: f'{tool.upper()}_HTPASSWD' for tool in ('pma', 'mailpit', 'cron', 'backup')}
PROCESS_SECRETS = ('DATABASE_PASSWORD', 'DATABASE_ROOT_PASSWORD', 'DATABASE_MONITOR_PASSWORD',
                   'DBMONITOR_TOKEN', 'METRICS_TOKEN', *TOOL_PASSWORDS.values(), *PRESTASHOP_KEYS)


def blank_env_value(raw):
    """Whether a dotenv value is empty: '', "", or nothing before an inline comment."""
    raw = raw.strip()
    if raw[:1] in ('"', "'"):
        return raw[1:2] == raw[0]
    return raw == '' or raw.startswith('#')


def env_file_keys(env_files):
    """Keys the env files set, mapped to whether the last file leaves the value blank."""
    keys = {}
    for env_file in env_files:
        for key, raw in ENV_ENTRY.findall(env_file.read_text()):
            keys[key] = blank_env_value(raw)
    return keys


def validate_project_dockerfile(path):
    """A project Dockerfile may only extend the CORE PHP image, never replace it."""
    instructions = [line.strip() for line in path.read_text().splitlines()
                    if line.strip() and not line.strip().startswith('#')]
    first_from = next((line for line in instructions if line.upper().startswith('FROM ')), '')
    if not re.fullmatch(r'FROM\s+\$\{?SETUP_PHP_IMAGE\}?(\s+AS\s+\S+)?', first_from, re.I):
        raise ValueError(f"{path} must extend the CORE image: start with 'ARG SETUP_PHP_IMAGE' and 'FROM ${{SETUP_PHP_IMAGE}}'")


def limit_suggestion(error):
    """Values to start from, read from the Docker engine the command targets."""
    try:
        result = subprocess.run(["docker", "info", "--format", "{{.MemTotal}} {{.NCPU}}"],
                                capture_output=True, text=True, timeout=20)
        total, cpus = (int(v) for v in result.stdout.split())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return f"Set them in env/{error.environment}.env (Docker host size unknown)."
    lines = [f"{key}={value}" for key, value in suggested_limits(total, cpus).items() if key in error.missing]
    return (f"Suggested for this Docker host ({total / 1024 ** 3:.1f} GiB RAM, {cpus} CPUs, one project; "
            f"divide when several projects share it):\n  " + "\n  ".join(lines))


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
        common_env = self.directory / "env/common.env"
        private_env = self.directory / f"env/{environment}.env"
        source = self.directory / "compose/base.yaml"
        overrides = [self.directory / "compose/common.yaml", self.directory / f"compose/{environment}.yaml"]
        if (self.directory / ".env").exists() or (self.directory / "compose.yaml").exists():
            raise ValueError("Root-level .env / compose.yaml sources are no longer supported; use env/ + compose/")
        if not common_env.is_file():
            raise ValueError(f"Missing {common_env}; create the project with create-project")
        if source.is_file() and LEGACY_CORE_INCLUDE.search(source.read_text()):
            raise ValueError(f"{source} includes setup's core services; the runner loads them now. Remove the include "
                             "(and the network), set DATABASE_ENGINE=mariadb for MariaDB, keep only real overrides")
        if not private_env.is_file():
            raise ValueError(f"Create {private_env} from its example")
        self.env_files = [DOCKER_ROOT / ".env", common_env, private_env]
        # Do not let a previous project's shell exports silently select this stack: env files win,
        # except a secret they leave blank, which a pipeline supplies as process environment.
        self.process_env = os.environ.copy()
        # Outside a pipeline a stage/prod command takes the Deployment variables from the stack's store.
        from project_secret_store import fill_from_store
        fill_from_store(self.directory, environment, self.process_env)
        for key, blank in env_file_keys(self.env_files).items():
            if not (blank and key in PROCESS_SECRETS and self.process_env.get(key)):
                self.process_env.pop(key, None)
        for key in ("COMPOSE_PROJECT_NAME", "COMPOSE_FILE", "COMPOSE_PROFILES",
                    "COMPOSE_ENV_FILES", "ENV_FILE"):
            self.process_env.pop(key, None)
        for key in (*DEFAULTS, *LEGACY_RESOURCE_KEYS):
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
                                        else DOCKER_ROOT / "docker/php-fpm/project"),
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
        profile = settings['PROFILE']
        if profile == 'ps': profile = 'prestashop'
        settings['PROFILE'] = profile
        self.process_env['PROFILE'] = profile
        # DOMAIN may list several hostnames; from here on it is the primary one (project_domains).
        from project_domains import apply_domains
        apply_domains(self, settings)
        if environment in ('stage', 'prod') and settings.get('APP_UID'):
            # Server containers run as the hosting account (e.g. the DirectAdmin user), not the builder.
            if not (str(settings['APP_UID']).isdigit() and str(settings.get('APP_GID', '')).isdigit()):
                raise ValueError('APP_UID and APP_GID must both be numeric ids of the account on the server')
            self.process_env.update(HOST_UID=str(settings['APP_UID']), HOST_GID=str(settings['APP_GID']))
        if environment in ('stage', 'prod') and settings.get('DEPLOY_CONTEXT'):
            if not settings.get('APP_UID'):
                raise ValueError(f'DEPLOY_CONTEXT runs ENV={environment} on a server: set APP_UID and APP_GID '
                                 'to the hosting account there')
            self.process_env['DOCKER_CONTEXT'] = settings['DEPLOY_CONTEXT']
        try:
            policy = resolve_policy(settings, environment)
        except MissingLimits as error:
            raise ValueError(f"{error}\n{limit_suggestion(error)}") from None
        settings.update(policy)
        self.process_env.update(policy)
        if settings['DATABASE_AS_CODE'] == 'off':
            settings['SCHEMA_DIRECTORY'] = ''  # every database-as-code step checks this one setting
        elif not settings['SCHEMA_DIRECTORY'] and self.directory.is_relative_to(self.root):
            settings['SCHEMA_DIRECTORY'] = str((self.directory / 'database/schema').relative_to(self.root))
        # The table registry lives beside the schema; the backup service mounts that directory.
        schema = (self.root / settings['SCHEMA_DIRECTORY']) if settings['SCHEMA_DIRECTORY'] else self.directory / 'database/schema'
        self.process_env['PROJECT_DATABASE_DIRECTORY'] = str(schema.resolve().parent)
        for key, attribute in [('DATA_DIRECTORY', 'data_directory'), ('APP_SOURCE_DIRECTORY', 'web_directory')]:
            if settings[key]:
                path = (self.root / settings[key]).resolve()
                if not path.is_relative_to(self.root):
                    raise ValueError(f'{key} must stay inside the project')
                if environment == 'test' and not path.is_relative_to(self.directory / '.generated/test'):
                    raise ValueError('Test data and code must stay inside .generated/test')
                setattr(self, attribute, path)
        self.process_env.update(PROJECT_DATA_DIRECTORY=str(self.data_directory), PROJECT_WEB_DIRECTORY=str(self.web_directory))
        if (self.directory / "Dockerfile").is_file():
            validate_project_dockerfile(self.directory / "Dockerfile")
        # Release source for the CORE PHP layer (see docker/Dockerfile): PrestaShop only.
        if profile != 'prestashop':
            from project_prestashop import generic_refusal
            for key in ("PS_RELEASE_PATHS", "PS_ADMIN_DIR"):
                if settings[key]:
                    raise generic_refusal(key)
            if settings["PS_RELEASE"] == "on":
                raise generic_refusal("PS_RELEASE=on")
        release_on = settings["PS_RELEASE"] == "on"
        if settings["PS_RELEASE"] not in ("", "on", "off"):
            raise ValueError("PS_RELEASE must be on or off")
        if release_on and not settings["PS_VERSION"]:
            raise ValueError("PS_RELEASE=on requires PS_VERSION")
        modern_release = release_on and not settings["PS_VERSION"].startswith("1.6")
        # Empty selects the base image (an empty /var/www/html) through the Compose default.
        self.process_env["PS_RELEASE_IMAGE"] = f"prestashop/prestashop:{settings['PS_VERSION']}" if modern_release else ""
        for key in ("PS_RELEASE_PATHS", "PS_ADMIN_DIR"):
            if any(part in ("", ".", "..") or part.startswith("/") for entry in settings[key].split()
                   for part in [entry] + entry.split("/")):
                raise ValueError(f"{key} entries must be relative paths inside the shop")
        if not settings["DATABASE_ROOT_PASSWORD"]:
            raise ValueError(f"Set DATABASE_ROOT_PASSWORD in {private_env} (make bootstrap generates one locally)")
        if settings["DATABASE_ROOT_PASSWORD"] == settings.get("DATABASE_PASSWORD"):
            raise ValueError("DATABASE_ROOT_PASSWORD must differ from DATABASE_PASSWORD")
        extra_schemas(settings)
        name = settings.get("PROJECT_NAME", "")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
            raise ValueError(f"Set a valid lowercase PROJECT_NAME in {common_env}")
        self.name = f"{name}-{environment}"
        services = settings["SERVICES"].split()
        unknown = [name for name in services if name not in SERVICE_CATALOGUE]
        if 'backup' in services and not settings['SCHEMA_DIRECTORY']:
            raise ValueError('The backup service edits the table registry of database as code, which is off (DATABASE_AS_CODE=off)')
        if unknown:
            raise ValueError(f"Unknown SERVICES entry: {', '.join(unknown)}; available: {' '.join(SERVICE_CATALOGUE)}")
        self.services = services
        self.process_env.update(resolve_metrics(settings))
        if profiles is None:
            selected = [p for p in re.split(r'[,\s]+', settings["PROFILES"]) if p]
            selected += [RUNTIME_PROFILES[name] for name in services if name in RUNTIME_PROFILES]
            profiles = ",".join(dict.fromkeys(selected))
        self.profiles = profiles
        self.command = base_command + ["--project-name", self.name]
        # The four core services come from setup; a project's base.yaml only adds or overrides.
        engine = settings["DATABASE_ENGINE"] or "mysql"
        if engine not in ("mysql", "mariadb"):
            raise ValueError("DATABASE_ENGINE must be mysql or mariadb")
        self.command += ["-f", str(DOCKER_ROOT / "docker/network.yml")]
        for name in ("nginx-proxy", "apache", engine, "php-fpm"):
            self.command += ["-f", str(DOCKER_ROOT / "docker" / name / "docker-compose.yml")]
        # The application's own settings (APP_ENVIRONMENT) for the services that run its code; before
        # the project's sources, so a name a project still lists itself keeps the project's definition.
        from project_app_environment import application_environment
        self.command += application_environment(self, settings, services)
        if source.is_file():
            self.command += ["-f", str(source)]
        # Selected catalogue services load right after the project's base sources.
        for name in services:
            self.command += ["-f", str(DOCKER_ROOT / "docker" / name / "docker-compose.yml")]
        # The metrics service reads the cron history only when the project runs cron.
        if 'metrics' in services and 'cron' in services:
            self.command += ["-f", str(DOCKER_ROOT / "docker/metrics/cron.yml")]
        # ... and what the last backup found about the table registry, with database as code.
        if 'metrics' in services and settings['SCHEMA_DIRECTORY']:
            self.command += ["-f", str(DOCKER_ROOT / "docker/metrics/registry.yml")]
        # Stage/prod keep their Deployment variables for the commands run outside a pipeline.
        from project_secret_store import secret_store, store_names
        self.command += secret_store(self, store_names(self.directory, environment))
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
        # The cron UI edits the crontab only where git is not the deploy path. Set before the
        # optional-mount overlay, which freezes the interpolated volume list.
        self.process_env['SETUP_CRON_CONFIG_ACCESS'] = 'rw' if environment in ('local', 'test') else 'ro'
        # The backup UI edits the table registry the same way: locally, servers through git.
        self.process_env['SETUP_BACKUP_ACCESS'] = self.process_env['SETUP_CRON_CONFIG_ACCESS']
        from project_domains import attach_domain_aliases
        attach_domain_aliases(self)
        from project_mounts import attach_optional_config_mounts
        attach_optional_config_mounts(self)
        active = json.loads(self.capture(['config', '--format', 'json']))['services']
        if environment in ('stage', 'prod'):
            for tool in ALLOWLISTED_TOOLS:
                if tool in active and not (settings[f'{tool.upper()}_ALLOWED_IPS'] or settings['ALLOWED_IPS']):
                    raise ValueError(f'{tool} on {environment} requires {tool.upper()}_ALLOWED_IPS or ALLOWED_IPS')
            if 'metrics' in active:
                check_token(settings, environment)
        self.process_env.update(SETUP_ENABLE_PMA='1' if 'pma' in active else '0',
                                SETUP_ENABLE_MAILPIT='1' if 'mailpit' in active else '0',
                                SETUP_ENABLE_CRON='1' if 'cron' in active else '0',
                                SETUP_ENABLE_DBMONITOR='1' if 'dbmonitor' in active else '0',
                                SETUP_ENABLE_BACKUP='1' if 'backup' in active else '0',
                                SETUP_ENABLE_METRICS='1' if 'metrics' in active else '0')
        # One fingerprint per built image, computed from that image's own inputs only.
        self.image_suffixes = image_suffixes(profile, self.model()['services'])
        self.process_env.update({suffix_variable(name): value for name, value in self.image_suffixes.items()})
        if environment == 'test':
            self.validate_test_isolation()

    def validate_test_isolation(self, profiles=None):
        from project_isolation import validate_test_model
        model = json.loads(self.capture(['config', '--format', 'json'], profiles=profiles))
        validate_test_model(self, model)

    def child_env(self, profiles=None):
        selected = getattr(self, "profiles", None) if profiles is None else profiles
        return {**self.process_env, **({"COMPOSE_PROFILES": selected} if selected is not None else {})}

    def compose_env(self, args, profiles=None):
        """Environment for one Compose command: images build here, running containers live on the target."""
        env = self.child_env(profiles)
        context = env.get("DOCKER_CONTEXT")
        if context and args and args[0] in LOCAL_COMPOSE_COMMANDS:
            env.pop("DOCKER_CONTEXT")
        elif context and args and args[0] in RELEASE_COMPOSE_COMMANDS:
            raise ValueError(f"ENV={self.environment} runs on {context} from the release model; "
                             f"use make deploy-images ENV={self.environment} instead of compose {args[0]}")
        return env

    def capture(self, args, profiles=None):
        result = subprocess.run(self.command + args, env=self.compose_env(args, profiles),
                                capture_output=True, text=True)
        if result.returncode:
            # Compose errors can include interpolated credentials; keep diagnostics private.
            raise ValueError(f"Compose {args[0]} failed (exit {result.returncode}); check the source YAML and env files")
        return result.stdout

    def run(self, args, profiles=None):
        if self.environment == 'test' and profiles is not None:
            self.validate_test_isolation(profiles)
        subprocess.run(self.command + args, env=self.compose_env(args, profiles), check=True)

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
                                           "phpstan", "phpstan-baseline", "phpcs", "e2e", "doctrine", "doctrine-diff", "doctrine-build",
                                           "restart", "composer", "npm", "cache-clear", "db-backup-prune", "image-prune", "runtime-info",
                                           "db-import", "db-import-plan", "db-restrict-grants", "db-monitor-user", "db-fixtures-load", "db-fixtures-plan", "schema-export", "schema-check",
                                           "schema-hook-install", "schema-commit", "schema-resolve", "db-migrate", "db-rollback", "migration-draft", "db-dataset", "db-load", "test-prepare", "test-sync", "phpunit", "release", "deploy-images", "deploy", "pipeline-deploy", "deploy-variables", "module-pull", "init", "doctor", "pull", "shell", "app-prepare", "setup", "ide-init", "ide-refresh", "db-backup", "db-pull", "db-prepare", "bootstrap", "test-init", "setup-info"])
    parser.add_argument("--docker-server", help="Existing PhpStorm Docker connection name for ide-init")
    parser.add_argument("--ide-config-directory", type=Path, help="PhpStorm configuration directory for Docker connection discovery")
    parser.add_argument("--command", help="QA command override, parsed as arguments (no shell)")
    parser.add_argument("--workdir", default=".", help="Composer/npm directory relative to application sources")
    parser.add_argument("--ref", default="HEAD", help="Committed schema baseline for doctrine-diff (default: HEAD)")
    parser.add_argument("--dump", help=".sql or .sql.gz dump for db-import / db-import-plan")
    parser.add_argument("--db-fixtures", help="Fixture set for db-fixtures-* or optional SQL after db-import hooks")
    parser.add_argument('--backup', action='store_true', help='Create a private backup first (db-import, db-load, db-pull import, deploy)')
    parser.add_argument('--dry', action='store_true', help='db-migrate/db-rollback: print the SQL without running it')
    parser.add_argument('--steps', type=int, default=1, help='db-rollback: how many applied migrations to roll back (default 1)')
    parser.add_argument('--to', help='db-rollback: roll back every migration applied after this one')
    parser.add_argument('--full', action='store_true', help='db-backup: dump the data of every table, ignoring registry skip rules')
    parser.add_argument('--skip', action='store_true', help='db-import: leave out the data of the tables the registry marks skip')
    parser.add_argument('--set', default='dev', help='db-dataset/db-load: data set name (default dev)')
    parser.add_argument('--replace', action='store_true', help='db-load: drop every table of a non-empty database first; '
                        'deploy-variables: also replace the Bitbucket / GitHub variables that exist')
    parser.add_argument('--from', dest='source', help='deploy-variables: create the missing variables from this dotenv file (0600); '
                        'db-pull: stage or prod (default prod)')
    parser.add_argument('--import', dest='import_now', action='store_true', help='db-pull: load the downloaded backup (as db-import)')
    parser.add_argument('--token-file', help='deploy-variables: access token file (0600); default BITBUCKET_ACCESS_TOKEN, '
                        'on GitHub GH_TOKEN or GITHUB_TOKEN')
    parser.add_argument('--names', action='store_true', help='deploy-variables: print the list only (no network)')
    parser.add_argument('--module', help='phpunit: run only this module suite (or shop)')
    parser.add_argument('--filter', dest='test_filter', help='phpunit: --filter pattern')
    parser.add_argument('--no-sync', action='store_true', help='phpunit: do not refresh the test application copy first')
    parser.add_argument('--allow-dirty', action='store_true', help='release: allow uncommitted setup changes (not for real releases)')
    parser.add_argument('--no-transfer', action='store_true', help='deploy-images: images are already on the target')
    parser.add_argument('--release-ref', help='deploy: shop tag or commit to deploy')
    parser.add_argument('--rollback', action='store_true', help='deploy: deploy the previous release again')
    parser.add_argument('--name', help='module-pull: module directory name; migration-draft: file name after the time (default schema)')
    parser.add_argument('--refresh-test', action='store_true', help='Stop and refresh the isolated test application checkout')
    parser.add_argument('--output', help='New backup destination (.sql or .sql.gz)')
    parser.add_argument('--timeout', type=int, default=90, help='Container readiness timeout in seconds')
    parser.add_argument('--service', help='One enabled service for logs/restart')
    parser.add_argument('--follow', action='store_true')
    parser.add_argument('--tail', type=int, default=100)
    parser.add_argument('--apply', action='store_true', help='Apply the pruning plan')
    parser.add_argument('--cache', action='store_true', help='image-prune: include the Docker build cache')
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error('--timeout must be positive')
    if args.tail < 0: parser.error('--tail must not be negative')
    if args.action == 'setup-info':
        setup_info()
        return 0
    try:
        from project_env_files import require_setup_version
        require_setup_version(args.docker_directory)
        if args.action == 'pipeline-deploy':
            # Before Project: the step writes env/<env>.env itself and runs each make step afresh.
            from project_pipeline import pipeline_deploy
            return pipeline_deploy(args.docker_directory, args.env, args.project_directory)
        if args.action == 'deploy-variables':
            from project_ci import deploy_variables
            return deploy_variables(args.docker_directory, args.env, source=args.source, replace=args.replace,
                                    token_file=args.token_file, names_only=args.names)
        if args.action in ("init", "ide-init", "bootstrap", "setup"):
            initialize_env(args.docker_directory, args.env)
            from project_bootstrap import ensure_generated_secrets
            ensure_generated_secrets(args.docker_directory, args.env)
        project = Project(args.docker_directory, args.env, args.project_directory, args.profiles)
        if args.action == 'runtime-info':
            from project_policy import DEFAULTS
            from project_prestashop import is_prestashop
            print(f'Environment: {project.environment}; project: {project.name}; profile: {project.settings["PROFILE"] or "generic"}')
            for key in DEFAULTS:
                if is_prestashop(project) or not key.startswith('PS_'):
                    print(f'{key}={project.settings[key]}')
        elif args.action == 'image-prune':
            from project_images import prune_images
            prune_images(project, args.apply, args.cache)
        elif args.action == 'db-backup-prune':
            from project_storage import prune_backups
            prune_backups(project, args.apply)
        elif args.action in ('restart', 'logs', 'composer', 'npm', 'cache-clear'):
            from project_commands import restart, logs, composer, npm, cache_clear
            if args.action == 'restart': restart(project, args.service, args.timeout)
            elif args.action == 'logs': logs(project, args.service, args.follow, args.tail)
            elif args.action == 'composer': composer(project, args.command, args.workdir)
            elif args.action == 'npm': npm(project, args.command, args.workdir)
            else: cache_clear(project)
        elif args.action == 'test-init':
            from project_bootstrap import initialize_test
            initialize_test(project, refresh=args.refresh_test)
        elif args.action == 'bootstrap':
            from project_bootstrap import bootstrap
            project = bootstrap(project)
        elif args.action == 'db-prepare':
            project.run(['up', '-d', '--no-deps', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', str(args.timeout), 'database'])
        elif args.action == 'db-monitor-user':
            from database_grants import create_monitor_user
            create_monitor_user(project)
        elif args.action == 'db-restrict-grants':
            from database_grants import restrict_grants
            restrict_grants(project)
        elif args.action == 'db-backup':
            # Server backups follow the table registry: structure of every table, data of imported ones.
            # An export follows the table registry (skip tables: structure only); full=1 dumps everything.
            backup_database(project, args.output, registry=not args.full)
        elif args.action == "ide-init":
            initialize_ide(project, args.docker_server, args.ide_config_directory)
        elif args.action == "ide-refresh":
            refresh_ide(project)
        elif args.action == "init":
            initialize_directories(project)
        elif args.action == "setup":
            from project_setup import run_setup
            project = run_setup(project, args.timeout)
        elif args.action == "app-prepare":
            from project_application import prepare_application
            prepare_application(project)
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
            # Locally built parents first: base, then the CORE project layer.
            project.run(["build", "php-fpm-base"], profiles="build_only")
            project.run(["build", "php-fpm-core"], profiles="build_only")
            project.run(["build"])
        elif args.action == "up":
            start_project(project, args.timeout)
        elif args.action in ("down", "ps"):
            project.run([args.action])
        elif args.action == "schema-commit":
            from database_schema import commit_schema
            commit_schema(project)
        elif args.action == "schema-resolve":
            from database_registry import resolve
            resolve(project)
        elif args.action == "db-migrate":
            from database_migrations_guarded import migrate
            migrate(project, dry=args.dry)
        elif args.action == "migration-draft":
            from database_migrations_guarded import draft_migrations
            draft_migrations(project, name=args.name or 'schema')
        elif args.action == "db-rollback":
            from database_migrations_guarded import rollback
            rollback(project, steps=args.steps, to=args.to, dry=args.dry)
        elif args.action == "db-dataset":
            from database_dataset import build_dataset
            build_dataset(project, args.set)
        elif args.action == "db-load":
            from database_dataset import load_dataset
            load_dataset(project, args.set, replace=args.replace, backup=args.backup)
        elif args.action == "test-prepare":
            from project_testing import prepare_tests
            prepare_tests(project, args.set)
        elif args.action == "test-sync":
            from project_bootstrap import initialize_test
            initialize_test(project, refresh=(project.directory / '.generated/test/initialized').is_file())
        elif args.action == "release":
            from project_release import build_release
            build_release(project, allow_dirty=args.allow_dirty)
        elif args.action == "deploy-images":
            from project_release import deploy_images
            deploy_images(project, transfer=not args.no_transfer)
        elif args.action == "deploy":
            from project_deploy import deploy_code
            deploy_code(project, args.release_ref, backup=args.backup, rollback=args.rollback)
        elif args.action == "module-pull":
            from project_deploy import module_pull
            module_pull(project, args.name)
        elif args.action == "phpunit":
            from project_testing import run_phpunit
            run_phpunit(project, args.module, args.test_filter, sync=not args.no_sync)
        elif args.action in ("schema-export", "schema-check", "schema-hook-install"):
            {"schema-export": export_schema, "schema-check": check_schema,
             "schema-hook-install": install_schema_hook}[args.action](project)
        elif args.action == "db-pull":
            from database_pull import pull
            target = pull(project, args.source or 'prod')
            if args.import_now:
                plan = plan_import(project, str(target))
                check_dump_tables(project, plan[0])
                import_database(project, plan, backup=args.backup)
            else:
                print(f'Load it: make db-import file={target}   (or next time: make db-pull import=1)')
        elif args.action in ("db-import", "db-import-plan"):
            plan = plan_import(project, args.dump)
            # Every dump table must be classified; the plan only reports, the import may ask.
            check_dump_tables(project, plan[0], interactive=False if args.action == "db-import-plan" else None)
            if args.db_fixtures is not None:
                plan += plan_fixtures(project, args.db_fixtures)
            if args.action == "db-import-plan":
                print(f"Database: {project.settings['DATABASE_NAME']}; environment: {project.environment}")
                for step in plan:
                    print(step)
            else:
                import_database(project, plan, backup=args.backup, skip=args.skip)
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
        elif args.action == "doctrine-build":
            project.run(["build", "php-doctrine-migrations"], profiles="doctrine")
        elif args.action == "doctrine-diff":
            from database_migrations import generate_migration
            generate_migration(project, args.ref)
        elif args.action == "doctrine":
            arguments = shlex.split(args.command or "status")
            project.run(["run", "--rm", "--no-deps", "php-doctrine-migrations"] + arguments, profiles="doctrine")
        else:
            service, profile, entrypoint, default = {
                "phpstan": ("php-phpstan", "phpstan", "make", "report"),
                "phpcs": ("php-cs", "phpcs", "make", "check"),
                "e2e": ("playwright", "playwright", "", project.settings["PLAYWRIGHT_COMMAND"]),
            }[args.action]
            if args.action == "e2e":
                # End-to-end tests write orders and customers: only ever against ENV=test.
                from project_testing import test_project
                project = test_project(project)
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
