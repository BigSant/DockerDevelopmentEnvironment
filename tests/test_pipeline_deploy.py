"""make pipeline-deploy: the CI deploy step, with git fixtures and a fake `make` (no Docker).

The fixture build roots have commits whose env/prod.env.example and config/mark carry their name. The
fake make logs the checkout it sees (commit subject, config, env file) and keeps setup's deploy history
(.generated/release/<env>/deployments.json) with setup's rollback rule. FAIL=<step> makes one step fail:
release, deploy-images, deploy (before the rsync, nothing recorded), after-rsync (recorded as failed).
Every run has the required Deployment variables (values containing VALUE, which no output may show):
Bitbucket's as process environment, GitHub's as the JSON of toJSON(secrets) / toJSON(vars).
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "docker")]
from project_env_files import parse_dotenv
from project_pipeline import deployment_variables

FAKE_MAKE = r'''#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path
args = sys.argv[1:]
options = dict(arg.split('=', 1) for arg in args[1:] if '=' in arg)
environment = options['ENV']
git = lambda *command: subprocess.run(['git', *command], capture_output=True, text=True).stdout.strip()
marks = [line[5:] for line in Path(f'env/{environment}.env').read_text().splitlines() if line.startswith('MARK=')]
with open(os.environ['MAKE_LOG'], 'a') as log:
    log.write(f"make {' '.join(args)} | head={git('log', '-1', '--format=%s')} "
              f"config={Path('config/mark').read_text().strip()} env={marks[-1] if marks else ''}\n")
with open(os.environ['MAKE_LOG'] + '.env', 'a') as log:
    keys = os.environ.get('LOG_KEYS', 'DOCKER_HOST MAKEFLAGS MAKELEVEL SETUP_DIRECTORY').split()
    log.write(json.dumps({key: os.environ.get(key) for key in keys}) + '\n')
target, fail = args[0], os.environ.get('FAIL', '')
if fail == target:
    print(f'fake make: {target} failed', file=sys.stderr)
    sys.exit(2)
if target != 'deploy':
    sys.exit(0)
history = Path(f'.generated/release/{environment}/deployments.json')
entries = json.loads(history.read_text()) if history.is_file() else []
if options.get('rollback') == '1':
    done = [e for e in entries[:-1] if not e.get('failed') and e['commit'] != entries[-1]['commit']]
    entry = {'ref': done[-1]['ref'], 'commit': done[-1]['commit'], 'rollback': True}
else:
    separate = Path('public/.git').exists()
    commit = git('-C', 'public', 'rev-parse', options['ref'] + '^{commit}') if separate else git('rev-parse', 'HEAD')
    entry = {'ref': options['ref'], 'commit': os.environ.get('FAKE_DEPLOYED') or commit, 'rollback': False}
if fail == 'after-rsync':
    entry['failed'] = 'after-rsync'
history.parent.mkdir(parents=True, exist_ok=True)
history.write_text(json.dumps(entries + [entry]))
sys.exit(3 if fail == 'after-rsync' else 0)
'''

REQUIRED = {"DATABASE_PASSWORD": "db-VALUE-1", "DATABASE_ROOT_PASSWORD": "root-VALUE-2",
            "MEILISEARCH_MASTER_KEY": "meili-VALUE-3", "METRICS_TOKEN": "metrics-VALUE-4",
            "HTPASSWD_WEB": "web:VALUE-5", "HTPASSWD_API": "api:VALUE-6"}
COMMON = """\
PROJECT_NAME=fixture
PROFILE=
SERVICES=metrics
DEPLOY_REQUIRED_VARIABLES="MEILISEARCH_MASTER_KEY HTPASSWD_WEB HTPASSWD_API"
DEPLOY_OPTIONAL_VARIABLES=HTPASSWD_BUILDER
DEPLOY_PLAIN_VARIABLES=GRAFANA_BASE_URL
APP_ENVIRONMENT="
  ERP_API_KEY GRAFANA_TOKEN
  GRAFANA_BASE_URL LLM_MODEL
"
LLM_MODEL=claude
"""


def git(directory, *args):
    return subprocess.run(["git", "-C", str(directory), *args], check=True, capture_output=True, text=True).stdout.strip()


class Fixture(unittest.TestCase):
    rerun = "run the pipeline on commit {commit}"

    def ci_environment(self):
        """The provider's own variables and the secrets, as a Bitbucket deployment step has them."""
        return {"BITBUCKET_DEPLOYMENT_ENVIRONMENT": "production", "BITBUCKET_COMMIT": self.pipeline_commit, **self.secrets}

    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="pipeline-", dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        (self.work / "bin").mkdir()
        (self.work / "bin/make").write_text(FAKE_MAKE)
        (self.work / "bin/make").chmod(0o755)
        self.log = self.work / "make.log"

    def repository(self, name, commits):
        """A bare origin with one commit per (subject, {path: text}); returns {subject: commit}."""
        origin = self.work / f"{name}.git"
        seed = self.work / f"{name}-seed"
        git(self.work, "init", "-q", "--bare", str(origin))
        git(self.work, "init", "-q", str(seed))
        for key, value in (("user.email", "t@example.invalid"), ("user.name", "T"), ("commit.gpgsign", "false"),
                           ("tag.gpgsign", "false")):
            git(seed, "config", key, value)
        made = {}
        for subject, files in commits:
            for path, text in files.items():
                (seed / path).parent.mkdir(parents=True, exist_ok=True)
                (seed / path).write_text(text)
            git(seed, "add", "-A")
            git(seed, "commit", "-q", "-m", subject)
            git(seed, "tag", subject.lower())
            made[subject] = git(seed, "rev-parse", "HEAD")
        git(seed, "push", "-q", "--tags", str(origin), "HEAD:refs/heads/master")
        return origin, made

    def run_step(self, expected, environment="local", **variables):
        self.log.write_text("")
        env = {"PATH": f"{self.work / 'bin'}:{os.environ['PATH']}", "HOME": os.environ.get("HOME", str(self.work)),
               "MAKE_LOG": str(self.log), **self.ci_environment()}
        for key, value in variables.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
        result = subprocess.run([sys.executable, str(ROOT / "docker/project.py"), "--docker-directory", str(self.app),
                                 "--project-directory", str(self.app.parent), "--env", environment, "pipeline-deploy"],
                                env=env, capture_output=True, text=True)
        self.output = result.stdout + result.stderr
        self.assertEqual(result.returncode, expected, self.report())
        self.assertNotIn("VALUE", self.output)
        return self.output

    def report(self):
        return f"output:\n{self.output}\nmake log:\n{self.log.read_text()}"

    def make_log(self):
        return self.log.read_text()

    def assert_log(self, text):
        self.assertIn(text, self.make_log(), self.report())

    def assert_out(self, text):
        self.assertIn(text, self.output, self.report())

    def assert_root(self, name):
        head = git(self.app, "log", "-1", "--format=%s")
        env = parse_dotenv((self.app / "env/prod.env").read_text())
        self.assertEqual((head, env.get("MARK")), (name, name), self.report())


class OneRepositoryTest(Fixture):
    """asana.local's layout: the code is part of the environment repository; deploys go to this host."""

    def setUp(self):
        super().setUp()
        example = "MARK={0}\nDEPLOY_CONTEXT=default\nDEPLOY_SSH=\nDEPLOY_PATH=/srv/fixture\n"
        commits = [(name, {"env/common.env": COMMON, "env/prod.env.example": example.format(name),
                           "config/mark": f"{name}\n", "public/index.php": f"<?php // {name}\n"}) for name in "ABC"]
        # D needs a newer setup; E adds a required Deployment variable.
        commits.append(("D", {"env/common.env": COMMON + "SETUP_MIN_VERSION=99.0.0\n", "env/prod.env.example": example.format("D"),
                              "config/mark": "D\n"}))
        commits.append(("E", {"env/common.env": COMMON.replace("HTPASSWD_API", "HTPASSWD_API NEW_SECRET"),
                              "env/prod.env.example": example.format("E"), "config/mark": "E\n"}))
        # F has an env file the reader refuses.
        commits.append(("F", {"env/common.env": COMMON, "env/prod.env.example": "MARK=F\nBROKEN='open\n", "config/mark": "F\n"}))
        origin, self.commits = self.repository("environment", commits)
        self.app = self.work / "root/app"
        git(self.work, "clone", "-q", str(origin), str(self.app))
        git(self.app, "checkout", "-q", "--detach", self.commits["A"])
        # As the first deploy by hand leaves it.
        (self.app / "env/prod.env").write_text((self.app / "env/prod.env.example").read_text())
        self.pipeline_commit = self.commits["C"]
        self.secrets = dict(REQUIRED)

    def test_deploys_rolls_back_and_restores_the_live_checkout(self):
        c = self.commits
        self.run_step(64, ROLLBACK="yes")
        self.assert_out("No earlier completed deploy")
        self.assert_root("A")

        self.run_step(0, REF=c["A"])
        self.assert_log(f"make deploy ENV=prod ref={c['A']} backup=1 | head=A config=A env=A")
        env = parse_dotenv((self.app / "env/prod.env").read_text())
        self.assertEqual((env["APP_UID"], env["APP_GID"]), (str(os.getuid()), str(os.getgid())))
        self.run_step(0, APP_REF=c["B"])
        self.assert_log("head=B config=B env=B")
        self.run_step(0, BACKUP="no")
        self.assert_log(f"make deploy ENV=prod ref={c['C']} backup=0 | head=C config=C env=C")
        self.assert_root("C")

        # rollback runs the previous release with its own config
        self.run_step(0, ROLLBACK="yes")
        self.assert_log("make deploy ENV=prod rollback=1 | head=B config=B env=B")
        self.assert_root("B")

        # failed after the rsync: the config returns to the live commit
        self.run_step(3, FAIL="after-rsync")
        self.assert_log(f"make deploy ENV=prod ref={c['C']} backup=1 | head=C config=C env=C")
        self.assert_out("back at the previously live commit")
        self.assert_root("B")

        # rollback after a failed deploy returns to the last completed release
        self.run_step(0, ROLLBACK="yes")
        self.assert_log("rollback=1 | head=B config=B env=B")
        self.assert_root("B")

        # failed before the rsync (nothing recorded)
        self.run_step(2, FAIL="deploy", REF=c["A"])
        self.assert_root("B")

        # failed release with IMAGES=yes: images untouched, config restored
        self.run_step(2, FAIL="release", IMAGES="yes")
        self.assert_log("make release ENV=prod | head=C config=C env=C")
        self.assertNotIn("deploy-images", self.make_log())
        self.assert_root("B")

        # failed after the images restarted: no automatic undo, the way back is printed
        self.run_step(2, FAIL="deploy", IMAGES="yes")
        self.assert_log("make deploy-images ENV=prod transfer=0 | head=C config=C env=C")
        self.assert_out(f"REF={c['B']} IMAGES=yes")
        self.assert_root("C")

        self.run_step(64, REF="no-such-ref")
        self.assert_out("Unknown ref no-such-ref")
        self.assert_root("C")

    def test_required_variables_missing_nothing_runs_and_the_names_are_given(self):
        self.run_step(64, DATABASE_ROOT_PASSWORD="", HTPASSWD_API=None)
        self.assert_out("Deployment variables missing or empty (Repository settings -> Deployments -> production): "
                        "DATABASE_ROOT_PASSWORD HTPASSWD_API")
        self.assert_out("make deploy-variables ENV=prod")
        self.assertEqual(self.make_log(), "")
        self.assert_root("A")
        self.run_step(64, ROLLBACK="yes", MEILISEARCH_MASTER_KEY="")
        self.assert_out("production): MEILISEARCH_MASTER_KEY")
        self.assertEqual(self.make_log(), "")

    def test_optional_variables_empty_warn_and_the_deploy_runs(self):
        self.run_step(0, ERP_API_KEY="", GRAFANA_TOKEN="")
        warning = next(line for line in self.output.splitlines() if line.startswith("Warning: empty Deployment variables"))
        self.assertEqual(warning.rsplit(":", 1)[1].split(),
                         ["DATABASE_MONITOR_PASSWORD", "HTPASSWD_BUILDER", "ERP_API_KEY", "GRAFANA_TOKEN", "GRAFANA_BASE_URL"])
        self.assert_log(f"make deploy ENV=prod ref={self.commits['C']} backup=1 | head=C config=C env=C")
        every = {name: "set-VALUE" for name in deployment_variables(self.app, "prod")}
        self.run_step(0, **every)
        self.assertNotIn("Warning", self.output)
        self.assert_root("C")

    def test_the_deployed_commits_own_lists_and_setup_version_are_checked_after_checkout(self):
        self.run_step(64, REF=self.commits["E"])
        self.assert_out("production): NEW_SECRET")
        self.assert_out(f"Nothing was deployed: the build root is back at the previously live commit {self.commits['A']}")
        self.assertEqual(self.make_log(), "")
        self.assert_root("A")
        self.run_step(64, REF=self.commits["D"])
        self.assert_out("This project needs setup 99.0.0 or newer")
        self.assertEqual(self.make_log(), "")
        self.assert_root("A")
        self.run_step(1, REF=self.commits["F"])
        self.assert_out("env/prod.env.example:2: BROKEN has no closing single quote")
        self.assert_out("Nothing was deployed: the build root is back")
        self.assert_root("A")

    def test_the_checkout_follows_what_setup_deployed(self):
        self.run_step(0, FAKE_DEPLOYED=self.commits["B"])
        self.assert_out(f"Warning: setup deployed {self.commits['B']}, not {self.commits['C']}; the build root follows it.")
        self.assert_root("B")

    def test_steps_run_with_this_setup_and_without_the_callers_make_and_docker_settings(self):
        self.run_step(0, DOCKER_HOST="tcp://localhost:2375", MAKEFLAGS="ref=elsewhere", MAKELEVEL="1")
        recorded = json.loads(Path(f"{self.log}.env").read_text().splitlines()[-1])
        self.assertEqual(recorded, {"DOCKER_HOST": None, "MAKEFLAGS": None, "MAKELEVEL": None, "SETUP_DIRECTORY": str(ROOT)})

    def test_refuses_outside_a_deployment_step_and_conflicting_settings(self):
        for variables, message in [({"BITBUCKET_DEPLOYMENT_ENVIRONMENT": "test"}, "Run this from a Staging or Production"),
                                   ({"BITBUCKET_DEPLOYMENT_ENVIRONMENT": None}, "BITBUCKET_DEPLOYMENT_ENVIRONMENT is not set"),
                                   ({"REF": "a", "APP_REF": "b"}, "REF, APP_REF name different refs"),
                                   ({"IMAGES": "maybe"}, "IMAGES must be yes or no")]:
            self.run_step(64, **variables)
            self.assert_out(message)
            self.assert_out("Nothing was changed.")
        self.run_step(64, environment="stage")
        self.assert_out("ENV=stage does not match the Bitbucket deployment environment production")
        # A deployment environment with another name needs ENV.
        self.run_step(0, environment="prod", BITBUCKET_DEPLOYMENT_ENVIRONMENT="production-eu")
        self.assert_log(f"make deploy ENV=prod ref={self.commits['C']} backup=1")
        self.assert_root("C")


class SeparateCodeRepositoryTest(Fixture):
    """Forsena's layout: the shop is its own repository in app/public; the target is another host."""

    def setUp(self):
        super().setUp()
        common = "PROJECT_NAME=shop\nPROFILE=prestashop\nPS_VERSION=9.0.2\nSERVICES=pma\n"
        example = "MARK={0}\nAPP_UID=1012\nAPP_GID=1013\nDEPLOY_CONTEXT=shop-prod\nDEPLOY_SSH=user@shop\nDEPLOY_PATH=/home/user/shop\n"
        origin, self.environments = self.repository("environment", [
            (name, {"env/common.env": common, "env/prod.env.example": example.format(name), "config/mark": f"{name}\n"})
            for name in ("E1", "E2")])
        shop, self.shop = self.repository("shop", [(name, {"index.php": f"<?php // {name}\n"}) for name in ("V1", "V2")])
        self.app = self.work / "root/app"
        git(self.work, "clone", "-q", str(origin), str(self.app))
        git(self.app, "checkout", "-q", "--detach", self.environments["E1"])
        (self.app / "env/prod.env").write_text((self.app / "env/prod.env.example").read_text())
        git(self.work, "clone", "-q", str(shop), str(self.app / "public"))
        git(self.app / "public", "checkout", "-q", "--detach", self.shop["V1"])
        self.pipeline_commit = self.environments["E2"]
        self.secrets = {"DATABASE_PASSWORD": "db-VALUE-1", "DATABASE_ROOT_PASSWORD": "root-VALUE-2",
                        **{name: f"{name}-VALUE" for name in ("PS_COOKIE_KEY", "PS_COOKIE_IV", "PS_NEW_COOKIE_KEY",
                                                              "PS_SECRET", "PS_API_PRIVATE_KEY", "PS_API_PUBLIC_KEY")}}

    def test_the_environment_follows_the_pipeline_commit_and_the_shop_ref_is_deployed(self):
        self.run_step(64)
        self.assert_out("Name the code tag or commit to deploy with REF (SHOP_REF)")
        self.run_step(64, SHOP_REF="v2", PS_API_PUBLIC_KEY="", PS_SECRET=None)
        self.assert_out("production): PS_SECRET PS_API_PUBLIC_KEY")
        self.run_step(64, SHOP_REF="nope")
        self.assert_out("Unknown ref nope in the code repository app/public")
        self.assert_root("E1")

        self.run_step(0, SHOP_REF="v1", IMAGES="yes")
        self.assert_log("make release ENV=prod | head=E2 config=E2 env=E2")
        self.assert_log("make deploy-images ENV=prod | head=E2")
        self.assert_log("make deploy ENV=prod ref=v1 backup=1 | head=E2 config=E2 env=E2")
        self.assert_root("E2")
        # The example names the hosting account; nothing is added for a remote target.
        self.assertEqual((self.app / "env/prod.env").read_text(), (self.app / "env/prod.env.example").read_text())
        self.assertEqual(git(self.app / "public", "rev-parse", "HEAD"), self.shop["V1"])

        self.run_step(0, REF="v2")
        self.assert_log("make deploy ENV=prod ref=v2 backup=1")
        self.run_step(0, ROLLBACK="yes")
        self.assert_log("make deploy ENV=prod rollback=1 | head=E2")
        history = json.loads((self.app / ".generated/release/prod/deployments.json").read_text())
        self.assertEqual(history[-1]["commit"], self.shop["V1"])

    def test_a_failed_deploy_returns_the_environment_checkout(self):
        self.run_step(2, SHOP_REF="v2", FAIL="deploy", BITBUCKET_COMMIT=self.environments["E2"])
        self.assert_out(f"The deploy failed: the build root is back at the previously live commit {self.environments['E1']}")
        self.assert_root("E1")
        self.run_step(2, SHOP_REF="v2", FAIL="deploy", IMAGES="yes")
        self.assert_out(self.rerun.format(commit=self.environments['E1']) + " with IMAGES=yes")
        self.assert_root("E2")


class GitHub:
    """A GitHub Actions job: environment: production, DEPLOY_ENVIRONMENT, GITHUB_SHA and the JSON contexts.

    The secrets JSON holds GitHub's own github_token as the secrets context does; plain names go to vars.
    """
    rerun = "run the workflow from a branch or tag at commit {commit}"
    plain = {"GRAFANA_BASE_URL"}

    def ci_environment(self):
        secrets = {name: value for name, value in self.secrets.items() if name not in self.plain}
        variables = {name: value for name, value in self.secrets.items() if name in self.plain}
        return {"GITHUB_ACTIONS": "true", "CI": "true", "DEPLOY_ENVIRONMENT": "production",
                "GITHUB_SHA": self.pipeline_commit, "GITHUB_REPOSITORY": "owner/fixture",
                "DEPLOY_SECRETS_JSON": json.dumps({"github_token": "ghs_T0KEN-VALUE", **secrets}),
                "DEPLOY_VARIABLES_JSON": json.dumps({"BUILD_ROOT": "", **variables})}

    def recorded(self, *keys, **variables):
        """The environment the last make step had for these keys."""
        self.run_step(0, LOG_KEYS=" ".join(keys), **variables)
        return json.loads(Path(f"{self.log}.env").read_text().splitlines()[-1])


class GitHubOneRepositoryTest(GitHub, OneRepositoryTest):
    """The same step from GitHub Actions: every inherited test runs again with GitHub's variables."""

    def test_required_variables_missing_nothing_runs_and_the_names_are_given(self):
        del self.secrets["HTPASSWD_API"]
        self.run_step(64, DATABASE_ROOT_PASSWORD="")
        self.assert_out("Environment secrets or variables missing or empty (Settings -> Environments -> production): "
                        "DATABASE_ROOT_PASSWORD HTPASSWD_API")
        self.assert_out("make deploy-variables ENV=prod lists every name and checks GitHub.")
        self.assertNotIn("The job passes no", self.output)
        self.assertEqual(self.make_log(), "")
        self.assert_root("A")
        self.run_step(64, DEPLOY_SECRETS_JSON=None, DEPLOY_VARIABLES_JSON=None)
        self.assert_out("The job passes no DEPLOY_SECRETS_JSON: give it env DEPLOY_SECRETS_JSON: ${{ toJSON(secrets) }}")
        self.assertEqual(self.make_log(), "")

    def test_refuses_outside_a_deployment_step_and_conflicting_settings(self):
        for variables, message in [({"DEPLOY_ENVIRONMENT": "test"}, "Run this from a Staging or Production"),
                                   ({"DEPLOY_ENVIRONMENT": None}, "DEPLOY_ENVIRONMENT is not set"),
                                   ({"GITHUB_ACTIONS": None, "DEPLOY_ENVIRONMENT": None},
                                    "neither BITBUCKET_DEPLOYMENT_ENVIRONMENT nor GITHUB_ACTIONS is set"),
                                   ({"GITHUB_SHA": None}, "Neither REF nor GITHUB_SHA names the commit"),
                                   ({"DEPLOY_SECRETS_JSON": '{"DATABASE_PASSWORD": "VALUE'}, "DEPLOY_SECRETS_JSON is not a JSON object"),
                                   ({"DEPLOY_VARIABLES_JSON": '["VALUE"]'}, "pass ${{ toJSON(vars) }} in the workflow")]:
            self.run_step(64, **variables)
            self.assert_out(message)
            self.assert_out("Nothing was changed.")
        self.assertEqual(self.make_log(), "")
        self.run_step(64, environment="stage")
        self.assert_out("ENV=stage does not match the GitHub deployment environment production")
        self.run_step(0, environment="prod", DEPLOY_ENVIRONMENT="Production-EU")
        self.assert_log(f"make deploy ENV=prod ref={self.commits['C']} backup=1")
        # GitHub's environment names are matched without case, as Bitbucket's.
        self.run_step(0, DEPLOY_ENVIRONMENT="Production")
        self.assert_root("C")

    def test_secrets_and_variables_reach_the_steps_by_name_only(self):
        self.secrets["GRAFANA_BASE_URL"] = "https://grafana.example/VALUE"
        recorded = self.recorded("DATABASE_PASSWORD", "GRAFANA_BASE_URL", "github_token", "BUILD_ROOT",
                                 "DEPLOY_SECRETS_JSON", "DEPLOY_VARIABLES_JSON")
        self.assertEqual(recorded, {"DATABASE_PASSWORD": "db-VALUE-1", "GRAFANA_BASE_URL": "https://grafana.example/VALUE",
                                    "github_token": None, "BUILD_ROOT": "", "DEPLOY_SECRETS_JSON": None,
                                    "DEPLOY_VARIABLES_JSON": None})

    def test_the_jobs_own_environment_wins_and_reserved_names_are_skipped(self):
        self.secrets.update({"REF": "secret-ref", "PATH": "/nowhere", "GITHUB_TOKEN": "x", "RUNNER_TEMP": "/x",
                             "lower_case": "kept-VALUE", "bad-name": "skipped"})
        # The workflow sets REF from its input, empty when not given.
        recorded = self.recorded("PATH", "GITHUB_TOKEN", "RUNNER_TEMP", "lower_case", "bad-name", "DATABASE_PASSWORD",
                                 REF="")
        self.assertNotEqual(recorded["PATH"], "/nowhere")
        self.assertEqual((recorded["GITHUB_TOKEN"], recorded["RUNNER_TEMP"], recorded["bad-name"]), (None, None, None))
        self.assertEqual(recorded["lower_case"], "kept-VALUE")
        # REF set by the job (even empty) wins over a secret of that name.
        self.assert_log(f"make deploy ENV=prod ref={self.commits['C']}")
        self.assertEqual(recorded["DATABASE_PASSWORD"], "db-VALUE-1")
        self.run_step(0, DATABASE_PASSWORD="from-the-job-VALUE", LOG_KEYS="DATABASE_PASSWORD", REF="")
        self.assertEqual(json.loads(Path(f"{self.log}.env").read_text().splitlines()[-1]),
                         {"DATABASE_PASSWORD": "from-the-job-VALUE"})

    def test_a_name_both_secret_and_variable_uses_the_secret(self):
        environment = self.ci_environment()
        variables = json.loads(environment["DEPLOY_VARIABLES_JSON"])
        variables["DATABASE_PASSWORD"] = "plain-VALUE"
        self.run_step(0, DEPLOY_VARIABLES_JSON=json.dumps(variables), LOG_KEYS="DATABASE_PASSWORD")
        self.assert_out("Warning: DATABASE_PASSWORD is both a secret and a configuration variable; the secret is used.")
        self.assertEqual(json.loads(Path(f"{self.log}.env").read_text().splitlines()[-1]),
                         {"DATABASE_PASSWORD": "db-VALUE-1"})


class GitHubSeparateCodeRepositoryTest(GitHub, SeparateCodeRepositoryTest):
    def test_the_environment_follows_the_pipeline_commit_and_the_shop_ref_is_deployed(self):
        self.run_step(64)
        self.assert_out("Name the code tag or commit to deploy with REF (SHOP_REF)")
        self.run_step(64, SHOP_REF="v1", GITHUB_SHA=None)
        self.assert_out("GITHUB_SHA is not set: the environment checkout follows the pipeline commit")
        del self.secrets["PS_SECRET"]
        self.run_step(64, SHOP_REF="v2", PS_API_PUBLIC_KEY="")
        self.assert_out("production): PS_SECRET PS_API_PUBLIC_KEY")
        self.assert_root("E1")
        self.secrets["PS_SECRET"] = "PS_SECRET-VALUE"
        self.run_step(0, SHOP_REF="v1", IMAGES="yes")
        self.assert_log("make deploy ENV=prod ref=v1 backup=1 | head=E2 config=E2 env=E2")
        self.assert_root("E2")
        self.run_step(0, SHOP_REF="v2")
        self.run_step(0, ROLLBACK="yes", REF="")
        self.assert_log("make deploy ENV=prod rollback=1 | head=E2")


class DeploymentVariablesListTest(unittest.TestCase):
    def project(self, common, example=""):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="variables-", dir=scratch)
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        (directory / "env").mkdir()
        (directory / "env/common.env").write_text(textwrap.dedent(common))
        (directory / "env/prod.env.example").write_text(textwrap.dedent(example))
        return directory

    def test_setup_names_follow_services_and_profile(self):
        listed = deployment_variables(self.project("PROFILE=prestashop\nPS_VERSION=8.1.5\nSERVICES='dbmonitor'\n"), "prod")
        self.assertEqual([name for name, v in listed.items() if v["required"]],
                         ["DATABASE_PASSWORD", "DATABASE_ROOT_PASSWORD", "PS_COOKIE_KEY", "PS_COOKIE_IV",
                          "PS_NEW_COOKIE_KEY", "PS_SECRET"])
        self.assertEqual([name for name, v in listed.items() if not v["required"]],
                         ["DATABASE_MONITOR_PASSWORD", "DBMONITOR_TOKEN"])
        generic = deployment_variables(self.project("PROFILE=\nPS_VERSION=9.0.0\n", "SERVICES=metrics\n"), "prod")
        self.assertEqual(list(generic), ["DATABASE_PASSWORD", "DATABASE_ROOT_PASSWORD", "DATABASE_MONITOR_PASSWORD",
                                         "METRICS_TOKEN"])
        self.assertFalse(generic["METRICS_TOKEN"]["required"])  # the IP allowlist guards /metrics

    def test_env_files_decide_what_comes_from_bitbucket(self):
        directory = self.project('APP_ENVIRONMENT="A_KEY B_URL C_FLAG"\nB_URL=https://b.example\nDEPLOY_PLAIN_VARIABLES=A_KEY\n',
                                 "C_FLAG=\nDATABASE_PASSWORD=\nDATABASE_ROOT_PASSWORD=in-the-file\n")
        listed = deployment_variables(directory, "prod")
        # A value in a file wins; a blank setup secret still comes from the process; a blank app name is off.
        self.assertEqual(list(listed), ["DATABASE_PASSWORD", "A_KEY"])
        self.assertFalse(listed["A_KEY"]["secured"])
        (directory / "env/prod.env.example").write_text("API_TOKEN=\n")
        (directory / "env/common.env").write_text("DEPLOY_REQUIRED_VARIABLES=API_TOKEN\n")
        with self.assertRaisesRegex(ValueError, "names API_TOKEN with an empty value, which hides the Deployment variable"):
            deployment_variables(directory, "prod")
        (directory / "env/common.env").write_text("DEPLOY_PLAIN_VARIABLES=TYPO\n")
        with self.assertRaisesRegex(ValueError, "DEPLOY_PLAIN_VARIABLES names TYPO, which no list names"):
            deployment_variables(directory, "prod")
        (directory / "env/common.env").write_text("DEPLOY_OPTIONAL_VARIABLES='lower-case'\n")
        with self.assertRaisesRegex(ValueError, "DEPLOY_OPTIONAL_VARIABLES lists variable names only"):
            deployment_variables(directory, "prod")


class TemplateTest(unittest.TestCase):
    def test_the_example_pipeline_runs_the_step_with_the_variables_it_reads(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML is not installed")
        model = yaml.safe_load((ROOT / "templates/pipelines/bitbucket-pipelines.yml").read_text())
        for name, deployment in (("deploy-stage", "Staging"), ("deploy-production", "Production")):
            variables, step = model["pipelines"]["custom"][name]
            self.assertEqual([v["name"] for v in variables["variables"]], ["REF", "IMAGES", "ROLLBACK", "BACKUP"])
            self.assertEqual(step["step"]["deployment"], deployment)
            self.assertIn('make -C "$BUILD_ROOT/app" pipeline-deploy', step["step"]["script"])
            self.assertIn("self.hosted", step["step"]["runs-on"])

    def test_the_github_workflow_runs_the_step_with_the_variables_it_reads(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML is not installed")
        from project_pipeline import DEPLOYMENT_ENVIRONMENTS, SECRETS_JSON, VARIABLES_JSON
        model = yaml.safe_load((ROOT / "templates/pipelines/github-deploy.yml").read_text())
        inputs = model[True]["workflow_dispatch"]["inputs"]  # YAML 1.1 reads the key on as true
        self.assertEqual(list(inputs), ["environment", "ref", "images", "rollback", "backup"])
        self.assertEqual(inputs["environment"]["options"], list(DEPLOYMENT_ENVIRONMENTS))
        for name, default in (("images", "no"), ("rollback", "no"), ("backup", "yes")):
            self.assertEqual((inputs[name]["type"], sorted(inputs[name]["options"]), inputs[name]["default"]),
                             ("choice", ["no", "yes"], default))
        job = model["jobs"]["deploy"]
        self.assertEqual(job["runs-on"], ["self-hosted", "linux", "setup.build"])
        self.assertEqual(job["environment"], "${{ inputs.environment }}")
        self.assertEqual(job["concurrency"], {"group": "deploy-${{ inputs.environment }}", "cancel-in-progress": False})
        self.assertEqual(model["permissions"], {"contents": "read"})
        [step] = job["steps"]
        self.assertEqual(step["env"], {
            "DEPLOY_ENVIRONMENT": "${{ inputs.environment }}", "REF": "${{ inputs.ref }}",
            "IMAGES": "${{ inputs.images }}", "ROLLBACK": "${{ inputs.rollback }}", "BACKUP": "${{ inputs.backup }}",
            "BUILD_ROOT": "${{ vars.BUILD_ROOT }}", SECRETS_JSON: "${{ toJSON(secrets) }}",
            VARIABLES_JSON: "${{ toJSON(vars) }}"})
        self.assertIn('make -C "$BUILD_ROOT/app" pipeline-deploy', step["run"])
        self.assertEqual(step["shell"], "bash")
        self.assertNotIn("checkout", json.dumps(job), "the build root, not a runner clone, is deployed")
        # The build root: vars.BUILD_ROOT, else setup-server's projects/<repository name in lower case>.
        script = step["run"].replace('make -C "$BUILD_ROOT/app" pipeline-deploy', 'echo "$BUILD_ROOT"')
        for variables, expected in (({}, "/srv/setup-server/projects/dockerdevelopmentenvironment"),
                                    ({"SETUP_SERVER_ROOT": "/opt/ss"}, "/opt/ss/projects/dockerdevelopmentenvironment"),
                                    ({"BUILD_ROOT": "/srv/elsewhere"}, "/srv/elsewhere")):
            environment = {"PATH": os.environ["PATH"], "GITHUB_REPOSITORY": "BigSant/DockerDevelopmentEnvironment",
                           "BUILD_ROOT": "", **variables}
            result = subprocess.run(["bash", "-e", "-c", script], env=environment, capture_output=True, text=True)
            self.assertEqual(result.stdout.strip(), expected, result.stderr)


if __name__ == "__main__":
    unittest.main()
