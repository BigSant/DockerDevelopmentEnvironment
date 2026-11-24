"""Extra stage-class environments (STAGE_ENVIRONMENTS in env/common.env): names, ENV, and stage's rules applied."""

import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "docker"), str(Path(__file__).resolve().parent)]
from create_project import scaffold
from project import Project
from project_environments import (deployment_environment, deployment_name, environments, is_server, is_stage_class,
                                  parse, require, server_environments)
from project_secret_store import store_names

# Limits and names only, as a server's example has them; the passwords are Deployment variables.
REMOTE = ('PHP_MEMORY=1g\nPHP_CPUS=1\nDATABASE_MEMORY=2g\nDATABASE_CPUS=1\nWEBSERVER_MEMORY=256m\nWEBSERVER_CPUS=1\n'
          'PROXY_MEMORY=128m\nPROXY_CPUS=0.5\nDOMAIN=demo.example.com\nDATABASE_USER=demo\nDATABASE_NAME=demo\n')
SECRETS = {"DATABASE_PASSWORD": "app-password-from-bitbucket", "DATABASE_ROOT_PASSWORD": "root-password-from-bitbucket"}


class NamesTest(unittest.TestCase):
    def test_the_list_takes_short_lowercase_names_that_setup_does_not_have(self):
        self.assertEqual(parse('demo, feature1  demo'), ('demo', 'feature1'))
        self.assertEqual(parse(''), ())
        for value, message in (('DEMO', 'lowercase'), ('m', 'lowercase'), ('1demo', 'lowercase'), ('de-mo', 'lowercase'),
                               ('a' * 17, 'lowercase'), ('demo prod', 'prod already exist'), ('test', 'test already exist')):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, message):
                parse(value)

    def test_an_extra_environment_is_a_server_one_of_the_stage_class(self):
        self.assertEqual([is_server(name) for name in ('local', 'test', 'stage', 'prod', 'demo')], [False, False, True, True, True])
        self.assertEqual([is_stage_class(name) for name in ('local', 'stage', 'prod', 'demo')], [False, True, False, True])
        settings = {'STAGE_ENVIRONMENTS': 'demo'}
        self.assertEqual(environments(settings), ('local', 'test', 'stage', 'prod', 'demo'))
        self.assertEqual(server_environments(settings), ('stage', 'prod', 'demo'))
        self.assertEqual(require(settings, 'demo'), 'demo')
        with self.assertRaisesRegex(ValueError, r'ENV=other is not an environment of this project.*lists demo'):
            require(settings, 'other')

    def test_the_deployment_environment_is_its_own_name(self):
        self.assertEqual([deployment_environment(name, ('demo',)) for name in ('staging', 'Production', 'DEMO', 'other')],
                         ['stage', 'prod', 'demo', None])
        self.assertEqual([deployment_name(name) for name in ('stage', 'prod', 'demo')], ['staging', 'production', 'demo'])


class ProjectTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        with contextlib.redirect_stdout(io.StringIO()):
            self.app = scaffold("extra-env", temporary.name)

    def list(self, value):
        with (self.app / "env/common.env").open("a") as env:
            env.write(f'STAGE_ENVIRONMENTS="{value}"\n')

    def test_an_unlisted_environment_is_refused_before_anything_runs(self):
        (self.app / "env/demo.env").write_text(REMOTE)
        with self.assertRaisesRegex(ValueError, "STAGE_ENVIRONMENTS"):
            Project(self.app, "demo")
        result = subprocess.run([sys.executable, str(ROOT / "docker/project.py"), "--docker-directory", str(self.app),
                                 "--env", "demo", "check"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ENV=demo is not an environment of this project", result.stdout + result.stderr)

    def test_a_bad_name_in_the_list_stops_every_command(self):
        self.list("demo Prod")
        with self.assertRaisesRegex(ValueError, "STAGE_ENVIRONMENTS names lowercase"):
            Project(self.app)

    def test_a_listed_environment_has_stages_rules_and_its_own_stack(self):
        self.list("demo")
        (self.app / "env/demo.env").write_text("DOMAIN=demo.example.com\n")
        with self.assertRaisesRegex(ValueError, "demo requires explicit container limits: .*DATABASE_MEMORY"):
            Project(self.app, "demo")  # stage's limits
        (self.app / "env/demo.env").write_text(REMOTE + "SERVICES=pma\n")
        with mock.patch.dict(os.environ, SECRETS), self.assertRaisesRegex(ValueError, "pma on demo requires PMA_ALLOWED_IPS"):
            Project(self.app, "demo")
        (self.app / "env/demo.env").write_text(REMOTE)
        with mock.patch.dict(os.environ, SECRETS):
            project = Project(self.app, "demo")
        self.assertEqual(project.name, "extra-env-demo")
        self.assertEqual(project.data_directory, self.app.parent / "data/demo")
        self.assertNotEqual(project.settings["MAIL_MODE"], "preserve", "only prod sends mail")
        self.assertIn("secrets", project.model()["services"], "the secret store of a server environment")
        self.assertEqual(store_names(self.app, "demo")[:2], ["DATABASE_PASSWORD", "DATABASE_ROOT_PASSWORD"])
        with self.assertRaisesRegex(ValueError, "loads only into local and test"):
            from database_load import plan_dataset
            plan_dataset(project, "dev", False)


if __name__ == "__main__":
    unittest.main()
