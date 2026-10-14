"""Release transfer into a disposable Docker-in-Docker target reached through a Docker context."""

import contextlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docker"))
from project_release import deploy_images

DIND = os.environ.get("SETUP_DIND_IMAGE")


@unittest.skipUnless(DIND, "Set SETUP_DIND_IMAGE (e.g. docker:27-dind) to run a disposable release target")
class ReleaseTargetTest(unittest.TestCase):
    def setUp(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.target = f"setup-target-{uuid.uuid4().hex[:8]}"
        # A throwaway Docker daemon stands in for the server: nothing of setup exists inside it.
        subprocess.run(["docker", "run", "-d", "--rm", "--privileged", "--name", self.target, "-e", "DOCKER_TLS_CERTDIR=",
                        "-p", f"127.0.0.1:{port}:2375", DIND], check=True, capture_output=True)
        self.addCleanup(subprocess.run, ["docker", "rm", "-f", self.target], capture_output=True)
        subprocess.run(["docker", "context", "create", self.target, "--docker", f"host=tcp://127.0.0.1:{port}"],
                       check=True, capture_output=True)
        self.addCleanup(subprocess.run, ["docker", "context", "rm", "-f", self.target], capture_output=True)
        for _ in range(60):
            if subprocess.run(["docker", "--context", self.target, "info"], capture_output=True).returncode == 0:
                break
            time.sleep(1)
        else:
            self.fail("target daemon did not start")
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="release-", dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def test_images_travel_to_the_target_and_run_there_without_setup(self):
        project = SimpleNamespace(environment="stage", directory=self.directory, settings={
            "APP_UID": "1012", "APP_GID": "1013", "DEPLOY_CONTEXT": self.target, "DEPLOY_PATH": "/srv/shop"})
        release = self.directory / ".generated/release/stage"
        release.mkdir(parents=True)
        (release / "images.lock.json").write_text(json.dumps({"project": "shop-stage", "images": {"alpine:latest": ""}}))
        model = {"name": "shop-stage", "services": {"app": {
            "image": "alpine:latest", "command": ["sh", "-c", "date > /data/started; sleep 300"],
            "volumes": [{"type": "bind", "source": "/srv/shop/data/app", "target": "/data",
                         "bind": {"create_host_path": True}}]}}}
        # The release image comes from this machine: a fresh CI runner has none.
        if subprocess.run(["docker", "image", "inspect", "alpine:latest"], capture_output=True).returncode:
            subprocess.run(["docker", "pull", "-q", "alpine:latest"], check=True, capture_output=True)
        self.assertNotIn(b"alpine", subprocess.run(["docker", "--context", self.target, "images"], capture_output=True).stdout)
        # DEPLOY_PATH is inside the throwaway daemon, which has no ssh: the file steps (environment files, runtime
        # directories) are the Deploy target's and tested on their own; this test is about images and the context.
        with contextlib.redirect_stdout(io.StringIO()), patch("project_release.release_model", return_value=model), \
                patch("project_release.sync_environment"), patch("project_release.prepare_runtime_directories"):
            self.assertEqual(deploy_images(project), ["alpine:latest"])
        self.assertFalse(any(release.glob("*compose*")), "the model (with env secrets) is piped, never stored")
        for _ in range(30):
            started = subprocess.run(["docker", "exec", self.target, "cat", "/srv/shop/data/app/started"], capture_output=True)
            if started.returncode == 0:
                break
            time.sleep(1)
        self.assertEqual(started.returncode, 0, "the service runs on the target with its data under DEPLOY_PATH")
        state = subprocess.run(["docker", "--context", self.target, "ps", "--format", "{{.Names}} {{.Image}}"],
                               capture_output=True, text=True).stdout
        self.assertIn("shop-stage-app-1 alpine:latest", state)
        listing = subprocess.run(["docker", "exec", self.target, "sh", "-c", "ls / /opt 2>/dev/null"], capture_output=True, text=True)
        self.assertNotIn("setup", listing.stdout, "no setup checkout on the target")


if __name__ == "__main__":
    unittest.main()
