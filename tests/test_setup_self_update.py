"""A deploy on a setup-server worker brings the shared setup up to the release its project needs."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "docker")]
from setup_self_update import newest_release, update_setup


def git(directory, *args):
    return subprocess.run(["git", "-C", str(directory), *args], check=True, capture_output=True, text=True).stdout.strip()


class NewestReleaseTest(unittest.TestCase):
    def test_the_newest_fitting_release_of_the_same_major(self):
        tags = ["v2.4.0", "v2.6.0", "v2.7.1", "v2.10.0", "v3.0.0", "v2.9.0-rc1", "stage-fix"]
        self.assertEqual(newest_release(tags, "2.4.0", "2.6.0"), "v2.10.0", "numeric order, not text")
        self.assertIsNone(newest_release(tags, "2.4.0", "2.11.0"))
        self.assertEqual(newest_release(tags, "3.0.0", "3.0.0"), "v3.0.0")


class UpdateSetupTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="self-update-", dir=scratch)
        self.addCleanup(temporary.cleanup)
        work = Path(temporary.name)
        origin, seed = work / "setup.git", work / "seed"
        git(work, "init", "-q", "--bare", str(origin))
        git(work, "init", "-q", str(seed))
        for key, value in (("user.email", "t@example.invalid"), ("user.name", "T"), ("commit.gpgsign", "false"),
                           ("tag.gpgsign", "false")):
            git(seed, "config", key, value)
        for version in ("2.4.0", "2.6.0", "2.7.0", "3.0.0"):
            (seed / "VERSION").write_text(version + "\n")
            git(seed, "add", "-A")
            git(seed, "commit", "-q", "-m", f"Release {version}")
            git(seed, "tag", f"v{version}")
        git(seed, "push", "-q", "--tags", str(origin), "HEAD:refs/heads/master")
        self.setup = work / "setup"
        git(work, "clone", "-q", str(origin), str(self.setup))
        git(self.setup, "checkout", "-q", "--detach", "v2.4.0")

    def test_the_checkout_moves_to_the_newest_release_of_its_major(self):
        self.assertEqual(update_setup("2.6.0", self.setup), ("2.4.0", "v2.7.0"))
        self.assertEqual((self.setup / "VERSION").read_text().strip(), "2.7.0")
        self.assertEqual(git(self.setup, "describe", "--tags"), "v2.7.0")

    def test_a_new_major_a_missing_release_and_local_changes_are_left_to_a_person(self):
        with self.assertRaisesRegex(ValueError, "a new MAJOR version"):
            update_setup("3.0.0", self.setup)
        with self.assertRaisesRegex(ValueError, "No setup release tag v2.x is 2.8.0 or newer"):
            update_setup("2.8.0", self.setup)
        (self.setup / "VERSION").write_text("edited\n")
        with self.assertRaisesRegex(ValueError, "local changes"):
            update_setup("2.6.0", self.setup)
        self.assertEqual(git(self.setup, "describe", "--tags", "--dirty"), "v2.4.0-dirty", "nothing was checked out")


if __name__ == "__main__":
    unittest.main()
