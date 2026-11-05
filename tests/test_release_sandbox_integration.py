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


def sandbox_scratch():
    """The fixtures' base: an isolated deploy refuses when any container mounts a directory above the release or the
    code tree (release_sandbox.check_trusted), and CI's other containers mount the checkout, so the fixtures live
    outside it, in a directory of this account only: $HOME/.cache/setup-sandbox-tests (0700), or
    SETUP_SANDBOX_TEST_DIR. Its ancestors must pass the same precondition (checked here, Docker aside)."""
    from release_sandbox import check_trusted
    base = Path(os.environ.get("SETUP_SANDBOX_TEST_DIR") or Path.home() / ".cache/setup-sandbox-tests")
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    base.chmod(0o700)
    check_trusted([base], None, run=lambda command, **options: subprocess.CompletedProcess(command, 0, "", ""))
    return base


@unittest.skipUnless(ENABLED, "release sandbox B test: SETUP_SANDBOX_TEST=1 (needs Docker)")
class SandboxDeployTest(unittest.TestCase):
    def setUp(self):
        self.scratch_directory = sandbox_scratch()
        base.DeployTest.setUp(self)

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

    def outside_with_secret(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("host secret\n")
        return outside

    def test_a_mount_root_that_is_a_link_is_refused(self):
        """Review 1: the target code directory (or the release) is a link to a directory of this machine."""
        from release_sandbox import Sandbox
        outside = self.outside_with_secret()
        self.generic()
        self.isolate()
        public = self.target / "app/public"
        public.rename(self.target / "app/public-real")
        public.symlink_to(outside)
        release = self.root / "app/.generated/release/stage/code/manual"
        release.mkdir(parents=True)
        (release / "index.php").write_text("<?php\n")
        with self.assertRaisesRegex(ValueError, "is a link or no directory"):
            Sandbox(self.project, release, (str(self.target), "app/public")).push(f"{release}/", f"{public}/", "-a")
        self.assertEqual(sorted(os.listdir(outside)), ["secret.txt"])
        public.unlink()
        (self.target / "app/public-real").rename(public)
        stolen = self.root / "app/.generated/release/stage/code/stolen"
        stolen.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "is a link or no directory"):
            Sandbox(self.project, stolen, (str(self.target), "app/public")).push(f"{stolen}/", f"{public}/", "-a")
        self.assertFalse((public / "secret.txt").exists(), "nothing of this machine was read into the target")

    def test_a_mount_root_swapped_after_the_check_is_caught_inside_the_container(self):
        """Review 1: between setup's check and Docker's mount, the target directory becomes a link to this machine's
        directory; the container compares what it got mounted with the checked inode and refuses."""
        from release_sandbox import Sandbox
        outside = self.outside_with_secret()
        self.generic()
        self.isolate()
        public = self.target / "app/public"
        release = self.root / "app/.generated/release/stage/code/manual"
        release.mkdir(parents=True)
        (release / "index.php").write_text("<?php // release\n")
        real_run = subprocess.run

        def swapping(command, **options):
            if command[:2] == ["docker", "run"] and not public.is_symlink():
                public.rename(self.target / "app/public-real")
                public.symlink_to(outside)
            return real_run(command, **options)

        sandbox = Sandbox(self.project, release, (str(self.target), "app/public"), run=swapping)
        with self.assertRaisesRegex(ValueError, "release sandbox could not push"):
            sandbox.push(f"{release}/", f"{public}/", "-a")
        self.assertEqual(sorted(os.listdir(outside)), ["secret.txt"], "nothing was written to this machine's directory")

    def test_the_release_build_container_checks_its_mounts_too(self):
        """Review 1: the release build's container (release PHP image; here the sandbox image) refuses a release
        directory swapped for a link between setup's check and the mount."""
        from project_deploy import in_release_image
        from release_sandbox import ensure_image
        outside = self.outside_with_secret()
        self.generic()
        self.isolate()
        release = self.root / "app/.generated/release/stage/code/manual"
        release.mkdir(parents=True)
        real_run = subprocess.run

        def swapping(command, **options):
            if command[:2] == ["docker", "run"] and not release.is_symlink():
                release.rename(release.with_name("manual-real"))
                release.symlink_to(outside)
            return real_run(command, **options)

        with patch("project_deploy.php_image", return_value=ensure_image()), patch("subprocess.run", swapping), \
                self.assertRaises(subprocess.CalledProcessError):
            in_release_image(self.project, release, "echo built > /var/www/html/built.txt")
        self.assertEqual(sorted(os.listdir(outside)), ["secret.txt"])

    def test_a_file_of_this_machine_hard_linked_into_the_target_keeps_its_content_and_mode(self):
        """Review 2: the target holds a hard link to a file outside it (same account): the deploy publishes new
        files and never changes an existing inode, so that file stays 0600 and unchanged."""
        public = self.generic()
        self.isolate()
        self.deploy(ref="g1")
        secret = self.root / "secret.php"
        for name, text in (("src/Kernel.php", "<?php // g1\n"), ("index.php", "<?php // other\n")):
            secret.unlink(missing_ok=True)
            secret.write_text(text)
            secret.chmod(0o600)
            (public / name).unlink(missing_ok=True)
            os.link(secret, public / name)
            self.deploy(ref="g1")
            self.assertEqual((secret.read_text(), secret.stat().st_mode & 0o777), (text, 0o600), name)
            self.assertNotEqual((public / name).stat().st_ino, secret.stat().st_ino, "the target got a file of its own")

    def test_staging_is_out_of_the_applications_reach(self):
        """Review 2 (re-check): the application races the deploy, looking for setup's staging directory in its code tree
        to plant a hard link to a file of this machine there (which a rename would then publish) or to chmod what is
        staged. Staging lives beside the code tree, in a directory no container of the project mounts: the
        application never sees it, and the published files are the release's own."""
        public = self.generic()
        self.isolate()
        secret = self.root / "secret.php"
        secret.write_text("<?php // host secret\n")
        secret.chmod(0o600)
        many = {f"src/generated/file{n}.php": f"<?php // {n}\n" for n in range(3000)}
        self.commit("big", {"src/Kernel.php": "<?php // big\n", **many})
        seen_in_code, seen_beside, stop = [], [], threading.Event()

        def application():
            while not stop.is_set():
                for name in os.listdir(public):
                    if name.startswith(".setup-staging-"):
                        seen_in_code.append(name)
                        for staged in (public / name).rglob("Kernel.php"):
                            try:
                                staged.unlink()
                                os.link(secret, staged)
                            except OSError:
                                pass
                        for staged in (public / name).rglob("*.php"):
                            try:
                                staged.chmod(0o666)
                            except OSError:
                                pass
                seen_beside.extend(name for name in os.listdir(public.parent) if name.startswith(".setup-staging-"))

        racer = threading.Thread(target=application)
        racer.start()
        try:
            self.deploy(ref="big")
        finally:
            stop.set()
            racer.join()
        self.assertEqual(seen_in_code, [], "the application never saw setup's staging")
        self.assertTrue(seen_beside, "staging was beside the code tree")
        self.assertEqual((public / "src/Kernel.php").read_text(), "<?php // big\n")
        self.assertNotEqual((public / "src/Kernel.php").stat().st_ino, secret.stat().st_ino)
        self.assertEqual((secret.read_text(), secret.stat().st_mode & 0o777), ("<?php // host secret\n", 0o600))
        self.assertEqual((public / "src/generated/file7.php").stat().st_mode & 0o777, 0o644)

    def test_module_versions_are_read_in_the_sandbox_never_through_a_link_or_a_fifo(self):
        """Review 4: PrestaShop's module versions come from the sandbox; a module file linked to a host secret or a
        FIFO yields nothing (and does not hang)."""
        from release_sandbox import Sandbox
        secret = self.root / "secret.php"
        secret.write_text("<?php $this->version = 'SECRET-9.9.9';\n")
        release = self.root / "app/.generated/release/stage/code/modules-test"
        for name in ("good", "evil", "fifo"):
            (release / "modules" / name).mkdir(parents=True)
        (release / "modules/good/good.php").write_text("<?php class Good { function __construct() { $this->version = '1.2.3'; } }\n")
        (release / "modules/evil/evil.php").symlink_to(secret)
        os.mkfifo(release / "modules/fifo/fifo.php")
        self.isolate()
        started = time.monotonic()
        self.assertEqual(Sandbox(self.project, release).module_versions(), {"good": "1.2.3"})
        self.assertLess(time.monotonic() - started, 60)
        self.assertEqual(Sandbox(self.project, release).release_directories("modules"), {"good", "evil", "fifo"})

    def test_a_leftover_container_mounting_the_code_trees_parent_refuses_the_deploy(self):
        """Re-check 1, against this machine's Docker: a container that is not running (created, never started; as one
        of a removed service) still holds a mount of the code tree's parent: the isolated deploy is refused."""
        self.generic()
        self.isolate()
        parent = self.target / "app"
        name = f"setup-trust-test-{os.getpid()}"
        subprocess.run(["docker", "create", "--name", name, "--network", "none", "-v", f"{parent}:/x", "alpine:3.20",
                        "true"], check=True, capture_output=True)
        self.addCleanup(subprocess.run, ["docker", "rm", "-f", name], capture_output=True)
        with self.assertRaisesRegex(ValueError, f"container {name} .created. mounts {parent}"):
            self.deploy(ref="g1")
        self.assertFalse((self.target / "app/public/src").exists(), "nothing was copied")

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

    def test_release_hardened_is_offered_only_for_a_target_on_this_machine(self):
        """Review 3: the probe says whether setup isolates this environment's deploy when the workflow asks for it
        (DEPLOY_REQUIRE_ISOLATION=1, which make deploy enforces or refuses); it reads the files the deploy reads:
        env/<env>.env when present, and setup-server's target files."""
        self.assertNotIn("release-hardened-v1", self.probe()[0], "without a project nothing is known of its target")
        local = "DOMAIN=stage.shop.test\nDEPLOY_PATH=/srv/shop-stage\n"
        app = self.project("", local)
        offered, _ = self.probe(f"PROJECT_DOCKER_DIRECTORY={app}", "ENV=stage")
        self.assertIn("release-hardened-v1", offered)
        self.assertIn("deploy-attempt-v1", offered)
        (app / "env/stage.env").write_text(local + "DEPLOY_SSH=shop@host.example\n")
        offered, reason = self.probe(f"PROJECT_DOCKER_DIRECTORY={app}", "ENV=stage")
        self.assertNotIn("release-hardened-v1", offered, "the effective env file sends the deploy to another server")
        self.assertIn("needs a deploy target on this machine", reason)
        (app / "env/stage.env").unlink()
        (app.parent / "target.stage.env").write_text("DEPLOY_SSH=shop@host.example\nDEPLOY_PATH=/home/shop\n")
        offered, reason = self.probe(f"PROJECT_DOCKER_DIRECTORY={app}", "ENV=stage")
        self.assertNotIn("release-hardened-v1", offered, "setup-server's target for stage is another server")

if __name__ == "__main__":
    unittest.main()
