"""Release isolation (RELEASE_ISOLATION=container) against real Docker: B level, SETUP_SANDBOX_TEST=1.

make deploy runs with test_deploy's fixtures (a shop repository, a target directory on this machine); the release build
step is replaced by a hostile one that plants what an agent branch's Composer scripts could: links to host files, hard
links, names with newlines, and an application that swaps its own directories during the deploy. Each attack must leave
every file of this machine outside the release and the target untouched. An isolated deploy of trusted content must
leave the same target, history and manifests as one on this machine.
"""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "docker"), str(Path(__file__).resolve().parent)]
import test_deploy as base
from fake_database import FakeDatabase
from project_deploy import deploy_code

ENABLED = os.environ.get("SETUP_SANDBOX_TEST") == "1"


@unittest.skipUnless(ENABLED, "release sandbox B test: SETUP_SANDBOX_TEST=1 (needs Docker)")
class SandboxDeployTest(unittest.TestCase):
    setUp = base.DeployTest.setUp
    commit = base.DeployTest.commit
    generic = base.GenericDeployTest.generic

    def isolate(self, on=True, **settings):
        # The target belongs to this account, as on a setup-server worker deploying to itself.
        self.project.settings.update({"APP_UID": str(os.getuid()), "APP_GID": str(os.getgid()), **settings})
        self.project.settings["RELEASE_ISOLATION"] = "container" if on else "none"
        (self.root / "app/config/deploy").mkdir(parents=True, exist_ok=True)
        (self.root / "app/config/deploy/build.sh").write_text("true\n")

    def deploy(self, plant=None, **options):
        """make deploy with a release build step that may plant things (plant(release, command), on this machine)."""
        def build(project, release, command, cache=None):
            self.calls.append(["image", command])
            if plant:
                plant(Path(release), command)

        self.project.database = FakeDatabase()
        with patch("project_deploy.php_image", return_value="php:test"), patch("project_deploy.in_release_image", build), \
                patch("database_backup.backup_database", lambda project, registry=False: None), \
                patch("database_schema.schema_report", lambda project: None), \
                patch("project_deploy.upgrade_modules", lambda project, release: []), \
                patch("project_application.plan_application", return_value=([], False)):
            return deploy_code(self.project, **options)

    def victim(self, name="victim.txt", text="host file\n"):
        path = self.root / name
        path.write_text(text)
        return path, path.stat().st_mtime_ns

    def assert_untouched(self, path, mtime, text="host file\n"):
        self.assertEqual(path.read_text(), text, f"{path} of this machine was changed")
        self.assertEqual(path.stat().st_mtime_ns, mtime, f"{path} of this machine was written")

    def tree(self, top):
        """(path, kind, content or link, mode) of everything below top, links not followed."""
        entries = []
        for directory, folders, names in os.walk(top):
            for name in sorted(folders + names):
                path = Path(directory) / name
                relative = path.relative_to(top).as_posix()
                if path.is_symlink():
                    entries.append((relative, "link", os.readlink(path), None))
                elif path.is_dir():
                    entries.append((relative, "dir", None, path.stat().st_mode & 0o7777))
                else:
                    entries.append((relative, "file", hashlib.sha256(path.read_bytes()).hexdigest(),
                                    path.stat().st_mode & 0o7777))
        return sorted(entries)

    def scenario(self, isolated):
        """Trusted content: three releases with removals, a link in the code and a protected path."""
        public = self.generic(DEPLOY_PROTECTED_PATHS="var/data/")
        self.isolate(isolated)
        self.deploy(ref="g1")
        os.symlink("src", self.shop / "linked")
        self.commit("g2", {"src/New.php": "<?php // new\n"}, removed=["config/local.php"])
        self.deploy(ref="g2")
        self.commit("g3", {"src/Kernel.php": "<?php // g3\n"}, removed=["src/New.php"])
        self.deploy(ref="g3")
        release = self.root / "app/.generated/release/stage"
        history = [{key: value for key, value in entry.items() if key != "at"}
                   for entry in json.loads((release / "deployments.json").read_text())]
        manifests = {path.name: json.loads(path.read_text()) for path in sorted((release / "manifests").glob("*.json"))}
        commits = [base.git_commit(self.shop, name) for name in ("g1", "g2", "g3")]
        return {"target": self.tree(public), "history": history, "manifests": manifests, "commits": commits}

    def test_an_isolated_deploy_of_trusted_content_matches_one_on_this_machine(self):
        here = self.scenario(isolated=False)
        self.setUp()
        isolated = self.scenario(isolated=True)
        # The fixtures make new commits: their ids differ, the file lists and trees must not.
        self.assertEqual(here["target"], isolated["target"])
        without_ids = lambda run: [{key: value for key, value in entry.items() if key != "commit"} for entry in run["history"]]
        self.assertEqual(without_ids(here), without_ids(isolated))
        in_order = lambda run: [run["manifests"][f"{commit[:12]}.json"] for commit in run["commits"]]
        self.assertEqual(in_order(here), in_order(isolated))

    def test_a_planted_link_to_a_host_file_is_never_written_or_followed(self):
        victim, mtime = self.victim()
        self.generic()
        self.isolate()
        self.commit("c1", {"composer.json": "{}\n"})

        def plant(release, command):
            for name in (".setup-build.sh", ".setup-release", "leak"):
                if not (release / name).is_symlink():
                    (release / name).unlink(missing_ok=True)
                    (release / name).symlink_to(victim)

        self.deploy(plant=plant, ref="c1")
        self.assert_untouched(victim, mtime)
        public = self.target / "app/public"
        self.assertEqual(os.readlink(public / "leak"), str(victim), "copied to the target as a link, never its content")

    def test_a_hard_link_to_a_host_file_is_never_written(self):
        victim, mtime = self.victim()
        self.generic()
        self.isolate()

        def plant(release, command):
            # A real build cannot reach a host file to link it; here the link exists, and setup must still never write it.
            for name in (".setup-release", "hard.txt"):
                if not (release / name).exists():
                    os.link(victim, release / name)

        self.deploy(plant=plant, ref="g1")
        self.assert_untouched(victim, mtime)
        release = self.root / "app/.generated/release/stage/code" / base.git_commit(self.shop, "g1")[:12]
        self.assertNotEqual(os.stat(release / ".setup-release").st_ino, victim.stat().st_ino,
                            "setup's file replaced the hard link")

    def test_a_newline_name_never_leads_a_removal_to_a_host_file(self):
        victim, mtime = self.victim()
        self.generic()
        self.isolate()

        def plant(release, command):
            if "g1" in (release / "src/Kernel.php").read_text():
                injected = release / "x\n" / str(victim.parent).lstrip("/") / victim.name
                injected.parent.mkdir(parents=True, exist_ok=True)
                injected.write_text("built\n")

        self.deploy(plant=plant, ref="g1")
        self.commit("g2", {"src/Kernel.php": "<?php // g2\n"})
        self.deploy(plant=plant, ref="g2")
        self.assert_untouched(victim, mtime)
        manifest = self.root / f"app/.generated/release/stage/manifests/{base.git_commit(self.shop, 'g1')[:12]}.json"
        self.assertFalse([name for name in json.loads(manifest.read_text()) if "\n" in name],
                         "a name setup would not trust stays out of the manifest")

    def test_the_application_swapping_its_directories_during_the_deploy_reaches_no_host_file(self):
        """A rename race: while the deploy copies and removes, the target's sub/ keeps turning into a link to a host
        directory (absolute, and relative out of the code tree) that holds a file of the removed name."""
        public = self.generic()
        self.isolate()
        outside = self.root / "host-directory"
        outside.mkdir()
        (outside / "old.php").write_text("host file\n")
        mtime = (outside / "old.php").stat().st_mtime_ns
        self.commit("r1", {"sub/old.php": "<?php // old\n"})
        self.deploy(ref="r1")
        self.commit("r2", {"src/Kernel.php": "<?php // r2\n"}, removed=["sub/old.php"])
        relative = os.path.relpath(outside, public)
        stop, swaps = threading.Event(), []

        def race():
            links = [str(outside), relative]
            while not stop.is_set():
                try:
                    (public / "sub").rename(public / "sub-real")
                    (public / "sub").symlink_to(links[len(swaps) % 2])
                    swaps.append(1)
                    time.sleep(0.0005)
                    (public / "sub").unlink()
                    (public / "sub-real").rename(public / "sub")
                except OSError:
                    pass

        racer = threading.Thread(target=race)
        racer.start()
        try:
            self.deploy(ref="r2")
        finally:
            stop.set()
            racer.join()
        if (public / "sub-real").exists():
            if (public / "sub").is_symlink():
                (public / "sub").unlink()
            (public / "sub-real").rename(public / "sub")
        self.assertGreater(len(swaps), 0)
        self.assert_untouched(outside / "old.php", mtime)

    def test_a_target_directory_the_application_links_to_the_host_never_receives_the_release(self):
        """PROFILE=prestashop, PS_RELEASE=on: the protected cache/ gets the release's files the target lacks. The
        application has made the target's cache a link to a host directory; on this machine rsync would write through
        it, in the sandbox the link leads nowhere."""
        host = self.root / "host-directory"
        host.mkdir()
        self.isolate(PS_RELEASE="on", PS_RELEASE_PATHS="")
        public = self.target / "app/public"
        (public / "cache").symlink_to(host)

        def plant(release, command):
            (release / "cache").mkdir(exist_ok=True)
            (release / "cache/index.php").write_text("<?php // the release's guard\n")

        try:
            self.deploy(plant=plant, ref="v1")
        except ValueError:
            pass  # the copy into a link that leads nowhere may fail; nothing of this machine may change
        self.assertEqual(sorted(os.listdir(host)), [], "nothing reached the host directory")

    def test_isolation_refuses_what_it_cannot_cover_before_anything_changes(self):
        self.generic()
        for settings, message in (({"DEPLOY_SSH": "shop@host.example"}, "needs a deploy target on this machine"),
                                  ({"RELEASE_BUILD_NETWORK": "host"}, "refuses RELEASE_BUILD_NETWORK=host")):
            with self.subTest(settings=settings):
                self.isolate(**settings)
                with self.assertRaisesRegex(ValueError, message):
                    self.deploy(ref="g1")
                self.assertFalse((self.root / "app/.generated/release/stage/deployments.json").exists())
                for key in settings:
                    self.project.settings.pop(key)


@unittest.skipUnless(ENABLED, "release sandbox B test: SETUP_SANDBOX_TEST=1 (needs Docker)")
class CapabilityTest(unittest.TestCase):
    def project(self, common, example):
        import tempfile
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="capability-", dir=scratch)
        self.addCleanup(temporary.cleanup)
        app = Path(temporary.name) / "app"
        (app / "env").mkdir(parents=True)
        (app / "env/common.env").write_text("PROJECT_NAME=shop\n" + common)
        (app / "env/stage.env.example").write_text(example)
        return app

    def probe(self, *arguments):
        result = subprocess.run(["make", "-s", "-f", str(ROOT / "docker/project.mk"), "setup-capabilities", *arguments],
                                capture_output=True, text=True, env={"PATH": os.environ["PATH"],
                                                                     "HOME": os.environ.get("HOME", "/tmp")})
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.split(), result.stderr

    def test_release_hardened_is_offered_only_for_an_isolated_environment_on_this_machine(self):
        self.assertNotIn("release-hardened-v1", self.probe()[0], "without a project nothing is known of its target")
        local = "DOMAIN=stage.shop.test\nDEPLOY_PATH=/srv/shop-stage\n"
        isolated = self.project("RELEASE_ISOLATION=container\n", local)
        offered, _ = self.probe(f"PROJECT_DOCKER_DIRECTORY={isolated}", "ENV=stage")
        self.assertIn("release-hardened-v1", offered)
        self.assertIn("deploy-attempt-v1", offered)
        plain = self.project("", local)
        offered, reason = self.probe(f"PROJECT_DOCKER_DIRECTORY={plain}", "ENV=stage")
        self.assertNotIn("release-hardened-v1", offered)
        self.assertIn("RELEASE_ISOLATION=container is not set", reason)
        remote = self.project("RELEASE_ISOLATION=container\n", local + "DEPLOY_SSH=shop@host.example\n")
        offered, reason = self.probe(f"PROJECT_DOCKER_DIRECTORY={remote}", "ENV=stage")
        self.assertNotIn("release-hardened-v1", offered)
        self.assertIn("needs a deploy target on this machine", reason)


if __name__ == "__main__":
    unittest.main()
