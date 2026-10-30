"""tools/release.py: the automatic release of every merge to master (VERSION and doc/RELEASES.md)."""

from pathlib import Path
import os
import itertools
import shutil
import subprocess
import sys
import tempfile
import textwrap
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


class ReleasePublicationTest(unittest.TestCase):
    """Execute the workflow's publication step against a real bare remote, without GitHub credentials."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.remote = self.root / 'remote.git'
        self.checkout = self.root / 'checkout'
        self.command('git', 'init', '--bare', str(self.remote))
        self.command('git', 'init', '-b', 'master', str(self.checkout))
        self.git('config', 'user.name', 'Release test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('remote', 'add', 'origin', str(self.remote))
        (self.checkout / 'tools').mkdir()
        (self.checkout / 'doc').mkdir()
        shutil.copyfile(ROOT / 'tools/release.py', self.checkout / 'tools/release.py')
        (self.checkout / 'VERSION').write_text('2.14.1\n')
        (self.checkout / 'doc/RELEASES.md').write_text(NOTES.format(marker=''))
        self.git('add', '.')
        self.git('commit', '-qm', 'A tested change')
        self.git('push', '-q', 'origin', 'master')
        self.before = self.git('rev-parse', 'HEAD')

    def command(self, *arguments):
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def git(self, *arguments):
        return self.command('git', '-C', str(self.checkout), *arguments)

    def publish(self, tested=None):
        workflow = (ROOT / '.github/workflows/release.yml').read_text()
        body = workflow.split('        run: |\n', 1)[1].splitlines(keepends=True)
        script = textwrap.dedent(''.join(itertools.takewhile(lambda line: not line.strip() or line.startswith('          '), body)))
        return subprocess.run(['bash', '-c', script], cwd=self.checkout,
                              env={**os.environ, 'TESTED': tested or self.before},
                              capture_output=True, text=True, timeout=30)

    def remote_ref(self, ref):
        return self.command('git', '-C', str(self.remote), 'rev-parse', '--verify', ref)

    def test_release_tag_master_and_local_channel_are_the_same_commit(self):
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        commit = self.remote_ref('refs/heads/master')
        self.assertNotEqual(commit, self.before)
        self.assertEqual(self.remote_ref('refs/tags/v2.14.2'), commit)
        self.assertEqual(self.remote_ref('refs/heads/deploy/local'), commit)

    def test_a_channel_push_rejection_publishes_neither_master_nor_the_tag(self):
        hook = self.remote / 'hooks/update'
        hook.write_text('#!/bin/sh\n[ "$1" != refs/heads/deploy/local ]\n')
        hook.chmod(0o755)
        result = self.publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.remote_ref('refs/heads/master'), self.before)
        for ref in ('refs/tags/v2.14.2', 'refs/heads/deploy/local'):
            result = subprocess.run(['git', '-C', str(self.remote), 'show-ref', '--verify', ref], capture_output=True)
            self.assertNotEqual(result.returncode, 0)

    def test_a_stale_ci_result_does_not_publish_anything(self):
        result = self.publish('0' * 40)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.remote_ref('refs/heads/master'), self.before)
        self.assertEqual((self.checkout / 'VERSION').read_text().strip(), '2.14.1')


if __name__ == "__main__":
    unittest.main()
