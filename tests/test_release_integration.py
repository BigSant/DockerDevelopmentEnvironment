"""Release transfer into a disposable Docker-in-Docker target reached through a Docker context."""

import contextlib
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import shutil
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
import project_release
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
        project = SimpleNamespace(environment="stage", directory=self.directory, name="shop-stage", settings={
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
        # directories, tool access, release info) are the Deploy target's and tested on their own; this test is
        # about images and the context.
        steps = (patch("project_release.release_model", return_value=model), patch("project_release.sync_environment"),
                 patch("project_release.prepare_runtime_directories"), patch("project_release.push_tool_access", return_value=False),
                 patch("project_release.publish_release_info"))
        # The stream compressed as from a classic image store (the target's docker load reads zstd), when zstd is here.
        first = io.StringIO()
        with contextlib.ExitStack() as stack, contextlib.redirect_stdout(first):
            for step in steps + (patch("project_release.saves_compressed", return_value=False),):
                stack.enter_context(step)
            self.assertEqual(deploy_images(project), ["alpine:latest"])
        self.assertIn("sending 1 images", first.getvalue())
        self.assertEqual(", zstd" in first.getvalue(), bool(shutil.which("zstd")))
        # Again: the target has the same image (whatever its image store calls it), nothing travels.
        again = io.StringIO()
        with contextlib.ExitStack() as stack, contextlib.redirect_stdout(again):
            for step in steps:
                stack.enter_context(step)
            self.assertEqual(deploy_images(project), ["alpine:latest"])
        self.assertIn("has the 1 images already: nothing to send", again.getvalue())
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

        # Another environment repository with the same PROJECT_NAME, its DEPLOY_PATH elsewhere: refused, untouched.
        other = SimpleNamespace(**{**vars(project), "settings": {**project.settings, "DEPLOY_PATH": "/srv/other"}})
        moved = json.loads(json.dumps(model).replace("/srv/shop/", "/srv/other/"))
        with contextlib.ExitStack() as stack, contextlib.redirect_stdout(io.StringIO()):
            stack.enter_context(patch("project_release.release_model", return_value=moved))
            with self.assertRaisesRegex(ValueError, "run from /srv/shop/data/app, outside DEPLOY_PATH /srv/other"):
                deploy_images(other)
        self.assertIn("shop-stage-app-1 alpine:latest", subprocess.run(
            ["docker", "--context", self.target, "ps", "--format", "{{.Names}} {{.Image}}"], capture_output=True, text=True).stdout)

    def test_a_build_the_target_has_is_tagged_and_unused_setup_images_are_removed(self):
        if subprocess.run(["docker", "image", "inspect", "alpine:latest"], capture_output=True).returncode:
            subprocess.run(["docker", "pull", "-q", "alpine:latest"], check=True, capture_output=True)
        # Two projects' names for one setup build, as two projects with the same PHP image build have.
        name = f"setup-it-{uuid.uuid4().hex[:8]}"
        subprocess.run(["docker", "build", "-q", "-t", f"{name}-first:1", "-"], input=b"FROM alpine:latest\nLABEL setup.managed=1\n",
                       check=True, capture_output=True)
        subprocess.run(["docker", "tag", f"{name}-first:1", f"{name}-second:1"], check=True)
        self.addCleanup(subprocess.run, ["docker", "rmi", f"{name}-first:1", f"{name}-second:1"], capture_output=True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            project_release.send_images(self.target, [f"{name}-first:1"])
            project_release.send_images(self.target, [f"{name}-second:1"])
        self.assertIn(f"{name}-second:1: the same build is on {self.target} as {name}-first:1; tagged there, not sent",
                      out.getvalue())
        self.assertEqual(project_release.image_fingerprint(f"{name}-second:1", self.target),
                         project_release.image_fingerprint(f"{name}-second:1"))
        # Kept while a container (even a stopped one) uses it or within the day; removed by both names after.
        later = datetime.now(timezone.utc) + timedelta(days=2)
        container = subprocess.run(["docker", "--context", self.target, "create", f"{name}-second:1"],
                                   check=True, capture_output=True, text=True).stdout.strip()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(project_release.prune_target_images(self.target, now=later), [])
            subprocess.run(["docker", "--context", self.target, "rm", container], check=True, capture_output=True)
            self.assertEqual(project_release.prune_target_images(self.target), [], "tagged within the day")
            self.assertEqual(sorted(project_release.prune_target_images(self.target, now=later)),
                             [f"{name}-first:1", f"{name}-second:1"])
        self.assertEqual(project_release.image_fingerprint(f"{name}-first:1", self.target), "")
        self.assertTrue(project_release.image_fingerprint(f"{name}-first:1"), "this machine's images stay")


if __name__ == "__main__":
    unittest.main()
