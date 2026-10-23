"""tools/release.py: the automatic release of every merge to master (VERSION and doc/RELEASES.md)."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import release  # noqa: E402

NOTES = """# Shared setup releases

Intro.

## Unreleased{marker}

- **Something.** It changed.

## 2.14.1

Older.
"""


class ReleaseToolTest(unittest.TestCase):
    def run_tool(self, marker="", body=None, version="2.14.1", *arguments):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "doc").mkdir()
        text = NOTES.format(marker=marker)
        if body is not None:
            text = text.replace("- **Something.** It changed.\n", body)
        (root / "doc/RELEASES.md").write_text(text)
        (root / "VERSION").write_text(version + "\n")
        result = subprocess.run([sys.executable, str(ROOT / "tools/release.py"), "--root", str(root), *arguments],
                                capture_output=True, text=True, timeout=30)
        return result, root

    def test_the_heading_says_how_far_the_version_moves(self):
        for marker, expected in (("", "2.14.2"), (" (patch)", "2.14.2"), (" (minor)", "2.15.0"), (" (major)", "3.0.0")):
            with self.subTest(marker=marker):
                result, root = self.run_tool(marker)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)
                self.assertEqual((root / "VERSION").read_text(), expected + "\n")
                notes = (root / "doc/RELEASES.md").read_text()
                self.assertIn(f"## Unreleased\n\n## {expected}\n\n- **Something.** It changed.\n\n## 2.14.1\n", notes)
                self.assertEqual(notes.count("## Unreleased"), 1, "a fresh, empty section for the next changes")

    def test_an_empty_section_still_releases_a_patch_saying_nothing_changes_for_projects(self):
        result, root = self.run_tool(body="\n")
        self.assertEqual(result.stdout.strip(), "2.14.2")
        self.assertIn(f"## 2.14.2\n\n{release.EMPTY_NOTE}\n\n## 2.14.1", (root / "doc/RELEASES.md").read_text())

    def test_check_changes_nothing_and_a_broken_file_is_refused(self):
        result, root = self.run_tool(" (minor)", None, "2.14.1", "--check")
        self.assertEqual(result.stdout.strip(), "2.15.0")
        self.assertEqual((root / "VERSION").read_text(), "2.14.1\n")
        self.assertIn("## Unreleased (minor)", (root / "doc/RELEASES.md").read_text())
        for marker, version, message in ((" (huge)", "2.14.1", "exactly one"), ("", "2.14", "MAJOR.MINOR.PATCH")):
            with self.subTest(marker=marker, version=version):
                result, root = self.run_tool(marker, None, version)
                self.assertEqual(result.returncode, 65)
                self.assertIn(message, result.stderr)
                self.assertEqual((root / "VERSION").read_text(), version + "\n")

    def test_the_repositorys_own_notes_and_version_release(self):
        text, version = release.release((ROOT / "doc/RELEASES.md").read_text(), (ROOT / "VERSION").read_text().strip())
        self.assertTrue(release.VERSION.fullmatch(version))
        self.assertIn(f"## Unreleased\n\n## {version}\n", text)


if __name__ == "__main__":
    unittest.main()
