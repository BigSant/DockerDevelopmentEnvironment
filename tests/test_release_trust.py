"""Release isolation's precondition: the attacker controls only the contents of the release and of the target code
tree, never their ancestors. Before an isolated deploy every ancestor of the build root, the release copies and the
code tree is a real directory of root or the setup account, not group- or world-writable, and no container of the
project mounts one of them; setup's own removals there go through a held descriptor of the trusted parent."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "docker"), str(Path(__file__).resolve().parent)]
from release_sandbox import check_trusted


class TrustTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="trust-", dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.top = Path(temporary.name)
        self.top.chmod(0o755)
        self.build = self.top / "build/app"
        (self.build / ".generated/release/stage/code").mkdir(parents=True)
        self.deploy = self.top / "srv/stage"
        self.code = self.deploy / "app/public"
        self.code.mkdir(parents=True)
        (self.deploy / "data").mkdir()
        for directory in (self.top / "build", self.build, self.top / "srv", self.deploy, self.deploy / "app"):
            directory.chmod(0o755)
        self.paths = [self.build.parent, self.build / ".generated/release/stage/code", self.code.parent]

    def model(self, *sources):
        return {"services": {"php-fpm": {"volumes": [{"type": "bind", "source": str(source), "target": "/x"}
                                                     for source in sources]}}}

    def docker(self, containers=(), volumes=None):
        """A stand-in for this machine's Docker: containers ({name, image, state, mounts}) and volumes ({name: inspect})."""
        volumes = volumes or {}

        def run(command, **options):
            if command[:3] == ["docker", "ps", "-aq"]:
                return subprocess.CompletedProcess(command, 0, "".join(f"{c['Name']}\n" for c in containers), "")
            if command[:2] == ["docker", "inspect"]:
                return subprocess.CompletedProcess(command, 0, json.dumps([c for c in containers if c["Name"] in command]), "")
            if command[:3] == ["docker", "volume", "inspect"]:
                found = [volumes[name] for name in command[3:] if name in volumes]
                return subprocess.CompletedProcess(command, 0 if found else 1, json.dumps(found), "")
            raise AssertionError(command)
        return run

    def check(self, model=None, containers=(), volumes=None, paths=None):
        check_trusted(paths or self.paths, model or self.model(self.code), run=self.docker(containers, volumes))

    @staticmethod
    def container(name, state, *mounts, image="php:8.4"):
        return {"Name": name, "Config": {"Image": image}, "State": {"Status": state}, "Mounts": list(mounts)}

    def test_a_trusted_layout_passes(self):
        self.check(self.model(self.code, self.deploy / "data", self.code / "var"))

    def test_a_group_or_world_writable_ancestor_is_refused(self):
        for mode, word in ((0o775, "group"), (0o757, "world")):
            with self.subTest(mode=oct(mode)):
                (self.deploy / "app").chmod(mode)
                with self.assertRaisesRegex(ValueError, f"{self.deploy / 'app'} is group- or world-writable"):
                    self.check(self.model(self.code))
                (self.deploy / "app").chmod(0o755)

    def test_a_symlinked_ancestor_is_refused(self):
        (self.top / "srv").rename(self.top / "srv-real")
        (self.top / "srv").symlink_to(self.top / "srv-real")
        with self.assertRaisesRegex(ValueError, f"{self.top / 'srv'} is a link"):
            self.check(self.model(self.code))

    def test_an_ancestor_of_another_account_is_refused(self):
        with patch("os.getuid", return_value=os.getuid() + 4242):
            with self.assertRaisesRegex(ValueError, "belongs neither to root nor to the setup account"):
                self.check(self.model(self.code))

    def test_a_container_mounting_an_ancestor_is_refused(self):
        for source in (self.deploy, self.deploy / "app", self.top / "srv", self.build / ".generated"):
            with self.subTest(source=source), \
                    self.assertRaisesRegex(ValueError, f"php-fpm mounts {source}, which holds"):
                self.check(self.model(self.code, source))

    def test_an_existing_container_mounting_an_ancestor_is_refused_whatever_its_state(self):
        """Re-check 1: not only the declared model: an exited container of a removed service still mounts the code
        tree's parent; a running one of another project mounts DEPLOY_PATH. A container mounting the code tree is fine."""
        bind = lambda source: {"Type": "bind", "Source": str(source), "Destination": "/x", "RW": True}
        self.check(containers=[self.container("shop-stage-php-fpm-1", "running", bind(self.code))])
        for name, state, source in (("shop-stage-old-worker-1", "exited", self.code.parent),
                                    ("other-project-1", "running", self.deploy),
                                    ("reader-1", "created", self.top / "build")):
            with self.subTest(container=name), self.assertRaisesRegex(ValueError, f"container {name} .{state}. mounts {source}"):
                self.check(containers=[self.container(name, state, bind(source))])

    def test_a_named_volume_bound_to_an_ancestor_is_refused(self):
        """Re-check 2: a local-driver volume with bind options (device) on an ancestor, declared in the model or used
        by an existing container; a volume driver setup cannot see into is refused for an isolated deploy."""
        bound = {"Name": "shop_code", "Driver": "local", "Options": {"type": "none", "o": "bind", "device": str(self.code.parent)}}
        model = self.model(self.code)
        model["services"]["php-fpm"]["volumes"].append({"type": "volume", "source": "code", "target": "/y"})
        model["volumes"] = {"code": {"name": "shop_code", "driver": "local",
                                     "driver_opts": {"type": "none", "o": "bind", "device": str(self.code.parent)}}}
        with self.assertRaisesRegex(ValueError, f"volume shop_code is bound to {self.code.parent}"):
            self.check(model)
        model["volumes"]["code"] = {"name": "shop_code", "driver": "rexray/ebs"}
        with self.assertRaisesRegex(ValueError, "volume shop_code uses the driver rexray/ebs"):
            self.check(model)
        model["volumes"]["code"] = {"name": "shop_code", "external": True}
        with self.assertRaisesRegex(ValueError, f"volume shop_code is bound to {self.code.parent}"):
            self.check(model, volumes={"shop_code": bound})
        mounted = {"Type": "volume", "Name": "shop_code", "Source": "/var/lib/docker/volumes/shop_code/_data",
                   "Destination": "/y", "RW": True}
        with self.assertRaisesRegex(ValueError, f"volume shop_code is bound to {self.code.parent}"):
            self.check(containers=[self.container("shop-stage-old-1", "exited", mounted)], volumes={"shop_code": bound})
        plain = {"Name": "shop_data", "Driver": "local", "Options": None}
        self.check(containers=[self.container("shop-stage-db-1", "running", {**mounted, "Name": "shop_data"})],
                   volumes={"shop_data": plain})

    def test_ancestors_that_do_not_exist_yet_are_forbidden_mounts_too(self):
        """Re-check 3: the code tree's parent is created by the deploy; a container already mounting that path would
        get the directory setup creates."""
        future = self.deploy / "app/stage/public"
        paths = [self.build.parent, self.build / ".generated/release/stage/code", future.parent]
        with self.assertRaisesRegex(ValueError, f"php-fpm mounts {future.parent}, which holds"):
            self.check(self.model(future, future.parent), paths=paths)

    def test_the_check_runs_again_once_setup_created_its_directories(self):
        """Re-check 3: after the sandbox created the code tree (and its parent) and the build the release copy."""
        import test_deploy as base
        from project_deploy import deploy_code
        holder = base.DeployTest("test_rsync_spares_writable_paths_and_target_only_modules")
        holder.setUp()
        self.addCleanup(holder.doCleanups)
        holder.project.settings.update({"RELEASE_ISOLATION": "container", "PROFILE": ""})
        calls = []

        class Sandbox:
            local, label = True, ""

            def __init__(self, project, release, target=None, run=None):
                calls.append(("sandbox", target))

            def release_directories(self, path):
                return set()

            def module_versions(self):
                return {}

            def push(self, *arguments):
                raise RuntimeError("stop after the sandbox was made")

        with patch("release_sandbox.check_trusted", lambda paths, model, **options: calls.append(("check", None))), \
                patch("release_sandbox.Sandbox", Sandbox), patch("project_deploy.build_code",
                                                                 return_value=(holder.root / "release", "1" * 40)), \
                self.assertRaisesRegex(RuntimeError, "stop after"):
            deploy_code(holder.project, ref="v1")
        self.assertEqual([kind for kind, _ in calls], ["check", "sandbox", "check"],
                         "again once the code tree and its parent exist, before anything is copied")

    def test_an_isolated_deploy_is_refused_before_anything_when_the_precondition_fails(self):
        import test_deploy as base
        holder = base.DeployTest("test_rsync_spares_writable_paths_and_target_only_modules")
        holder.setUp()
        self.addCleanup(holder.doCleanups)
        holder.project.settings.update({"RELEASE_ISOLATION": "container", "PROFILE": ""})
        holder.target.parent.chmod(0o777)
        self.addCleanup(holder.target.parent.chmod, 0o755)
        with patch("release_sandbox.Sandbox", side_effect=AssertionError("no container before the check")), \
                patch("project_deploy.build_code", side_effect=AssertionError("nothing built before the check")), \
                self.assertRaisesRegex(ValueError, "is group- or world-writable"):
            from project_deploy import deploy_code
            deploy_code(holder.project, ref="v1")

    def test_setups_own_removals_go_through_a_held_descriptor(self):
        """The build hook's temporary file and old release copies are removed relative to a descriptor of their
        trusted parent: a parent that is a link is refused, nothing is removed through it."""
        from types import SimpleNamespace
        from project_deploy import _run_hook, prune_releases
        outside = self.top / "outside"
        outside.mkdir()
        (outside / ".setup-build.sh").write_text("keep\n")
        linked = self.build / ".generated/release/stage/code/linked"
        linked.symlink_to(outside)
        project = SimpleNamespace(directory=self.build, environment="stage", settings={})
        hook = self.top / "build.sh"
        hook.write_text("true\n")
        with patch("project_deploy.in_release_image", lambda *args, **options: None), \
                self.assertRaisesRegex(OSError, "Too many levels of symbolic links|Not a directory"):
            _run_hook(project, linked, hook)
        self.assertEqual((outside / ".setup-build.sh").read_text(), "keep\n")
        code = self.build / ".generated/release/stage/code"
        code.rename(code.with_name("code-real"))
        code.symlink_to(outside)
        for number in range(5):  # more than KEEP_RELEASES, none in the history: prune would remove the oldest
            (outside / f"old-release-{number}").mkdir()
            os.utime(outside / f"old-release-{number}", (1000 + number, 1000 + number))
        with patch("project_deploy.history", return_value=[]), \
                self.assertRaisesRegex(OSError, "Too many levels of symbolic links|Not a directory"):
            prune_releases(project, keep=code / "new")
        self.assertEqual(len([path for path in outside.iterdir() if path.name.startswith("old-release")]), 5,
                         "nothing was removed through the link")


if __name__ == "__main__":
    unittest.main()
