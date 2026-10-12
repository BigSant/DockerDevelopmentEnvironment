"""The stage/prod secret store: the stack keeps its Deployment variables for commands outside a pipeline."""

import contextlib
import io
import json
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
from project_secret_store import container_name, fill_from_store, read_store, store_names

# Limits and names only: the passwords are Deployment variables, as on a server.
REMOTE = ('PHP_MEMORY=1g\nPHP_CPUS=1\nDATABASE_MEMORY=2g\nDATABASE_CPUS=1\nWEBSERVER_MEMORY=256m\nWEBSERVER_CPUS=1\n'
          'PROXY_MEMORY=128m\nPROXY_CPUS=0.5\nDOMAIN=demo.example.com\nDATABASE_USER=demo\nDATABASE_NAME=demo\n')
SECRETS = {"DATABASE_PASSWORD": "app-password-from-bitbucket", "DATABASE_ROOT_PASSWORD": "root-password-from-bitbucket",
           "SUPPLIER_TOKEN": "supplier-token-from-bitbucket"}


def inspect(env, code=0):
    """A docker inspect stand-in answering with one container's environment."""
    calls = []

    def run(command, **_):
        calls.append(command)
        stdout = json.dumps(["PATH=/usr/bin", *(f"{key}={value}" for key, value in env.items())])
        return subprocess.CompletedProcess(command, code, stdout if code == 0 else "", "")
    return run, calls


class SecretStoreTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        with contextlib.redirect_stdout(io.StringIO()):
            self.app = scaffold("store-demo", temporary.name)
        with (self.app / "env/common.env").open("a") as env:
            env.write('APP_ENVIRONMENT="SUPPLIER_TOKEN"\n')
        (self.app / "env/stage.env").write_text(REMOTE)
        patcher = mock.patch.dict(os.environ, {"CI": ""})
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in SECRETS:
            os.environ.pop(key, None)

    def test_the_store_keeps_the_deployment_variables_of_stage_and_prod_only(self):
        self.assertEqual(store_names(self.app, "stage"), ["DATABASE_PASSWORD", "DATABASE_ROOT_PASSWORD", "SUPPLIER_TOKEN"])
        self.assertEqual(store_names(self.app, "local"), [])
        self.assertEqual(container_name("store-demo", "prod"), "store-demo-prod-secrets")

    def test_outside_a_pipeline_missing_names_come_from_the_store(self):
        run, calls = inspect(SECRETS)
        env = {"DATABASE_PASSWORD": "given-by-the-caller"}
        self.assertEqual(fill_from_store(self.app, "stage", env, run), ["DATABASE_ROOT_PASSWORD", "SUPPLIER_TOKEN"])
        self.assertEqual(env, {"DATABASE_PASSWORD": "given-by-the-caller",
                               "DATABASE_ROOT_PASSWORD": "root-password-from-bitbucket",
                               "SUPPLIER_TOKEN": "supplier-token-from-bitbucket"})
        self.assertEqual(calls[0][-1], "store-demo-stage-secrets")
        self.assertNotIn("PATH", env, "only the store's names are taken")

    def test_a_pipeline_uses_its_own_values_only(self):
        # A variable removed in Bitbucket must not come back from the running stack.
        run, calls = inspect(SECRETS)
        env = {}
        with mock.patch.dict(os.environ, {"CI": "true"}):
            self.assertEqual(fill_from_store(self.app, "stage", env, run), [])
        self.assertEqual((env, calls), ({}, []))

    def test_local_and_a_missing_store_change_nothing(self):
        run, calls = inspect(SECRETS)
        self.assertEqual(fill_from_store(self.app, "local", {}, run), [])
        self.assertEqual(calls, [])
        missing, _ = inspect({}, code=1)
        env = {}
        self.assertEqual(fill_from_store(self.app, "stage", env, missing), [])
        self.assertEqual(env, {})

    def test_the_store_is_read_through_the_deploy_context(self):
        with (self.app / "env/stage.env").open("a") as env:
            env.write("DEPLOY_CONTEXT=shop-live\n")
        run, calls = inspect(SECRETS)
        read_store("store-demo-stage-secrets", "shop-live", run)
        fill_from_store(self.app, "stage", {}, run)
        for command in calls:
            self.assertEqual(command[:3], ["docker", "--context", "shop-live"])

    def test_the_stage_model_holds_references_in_a_sleeping_container_without_network(self):
        with mock.patch.dict(os.environ, SECRETS):
            project = Project(self.app, "stage")
        service = project.model()["services"]["secrets"]
        self.assertEqual(service["environment"], SECRETS)
        self.assertEqual(service["network_mode"], "none")
        self.assertEqual(service["entrypoint"], ["sleep", "infinity"])
        self.assertTrue(service["read_only"])
        self.assertNotIn("volumes", service)
        overlay = next(path for path in (self.app / ".generated").glob("secret-store-*.yaml"))
        text = overlay.read_text()
        self.assertIn("DATABASE_PASSWORD: ${DATABASE_PASSWORD-}", text)
        for value in SECRETS.values():
            self.assertNotIn(value, text, "the overlay holds references, never values")
        self.assertEqual(overlay.stat().st_mode & 0o077, 0)

    def test_local_has_no_store(self):
        project = Project(self.app, "local")
        self.assertNotIn("secrets", project.model()["services"])


if __name__ == "__main__":
    unittest.main()
