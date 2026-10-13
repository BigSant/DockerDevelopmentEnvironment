"""project.py's command line builds and parses every action (a clashing option breaks every make target)."""
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CommandLineTest(unittest.TestCase):
    def test_the_parser_builds_and_lists_the_migration_commands(self):
        result = subprocess.run([sys.executable, str(ROOT / "docker/project.py"), "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for action in ("db-migrate", "db-rollback", "migration-draft", "module-pull"):
            self.assertIn(action, result.stdout)


if __name__ == "__main__":
    unittest.main()
