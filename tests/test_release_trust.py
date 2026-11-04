"""Release isolation's precondition: the attacker controls only the contents of the release and of the target code
tree, never their ancestors. Before an isolated deploy every ancestor of the build root, the release copies and the
code tree is a real directory of root or the setup account, not group- or world-writable, and no container of the
project mounts one of them; setup's own removals there go through a held descriptor of the trusted parent."""

import os
from pathlib import Path
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

    def test_a_trusted_layout_passes(self):
        check_trusted(self.paths, self.model(self.code, self.deploy / "data", self.code / "var"))

    def test_a_group_or_world_writable_ancestor_is_refused(self):
        for mode, word in ((0o775, "group"), (0o757, "world")):
            with self.subTest(mode=oct(mode)):
                (self.deploy / "app").chmod(mode)
                with self.assertRaisesRegex(ValueError, f"{self.deploy / 'app'} is group- or world-writable"):
                    check_trusted(self.paths, self.model(self.code))
                (self.deploy / "app").chmod(0o755)

    def test_a_symlinked_ancestor_is_refused(self):
        (self.top / "srv").rename(self.top / "srv-real")
        (self.top / "srv").symlink_to(self.top / "srv-real")
        with self.assertRaisesRegex(ValueError, f"{self.top / 'srv'} is a link"):
            check_trusted(self.paths, self.model(self.code))

    def test_an_ancestor_of_another_account_is_refused(self):
        with patch("os.getuid", return_value=os.getuid() + 4242):
            with self.assertRaisesRegex(ValueError, "belongs neither to root nor to the setup account"):
                check_trusted(self.paths, self.model(self.code))

    def test_a_container_mounting_an_ancestor_is_refused(self):
        for source in (self.deploy, self.deploy / "app", self.top / "srv", self.build / ".generated"):
            with self.subTest(source=source), \
                    self.assertRaisesRegex(ValueError, f"php-fpm mounts {source}, which holds"):
                check_trusted(self.paths, self.model(self.code, source))

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
