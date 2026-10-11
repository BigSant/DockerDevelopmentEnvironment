"""Setup writes each env key under its section, sections in a fixed order."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "docker")]
from project_bootstrap import set_env_values


class EnvLayoutTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "local.env"

    def test_keys_written_one_by_one_end_up_grouped_and_ordered(self):
        for values in ({"DATABASE_ROOT_PASSWORD": "r"}, {"PS_SECRET": "s"}, {"DOMAIN": "demo.local"},
                       {"DATABASE_NAME": "demo"}, {"PS_COOKIE_KEY": "k"}, {"LOCALHOST_PORT": "3301"}):
            set_env_values(self.path, values)
        self.assertEqual(self.path.read_text(),
                         "# Address\nDOMAIN=demo.local\nLOCALHOST_PORT=3301\n\n"
                         "# Database\nDATABASE_NAME=demo\nDATABASE_ROOT_PASSWORD=r\n\n"
                         "# PrestaShop keys\nPS_COOKIE_KEY=k\nPS_SECRET=s\n")

    def test_existing_lines_comments_and_unknown_keys_are_kept_apart(self):
        self.path.write_text("# Copy me.\n\n# Database\n# The user only reaches its own DB.\nDATABASE_USER=demo\n"
                             "\n# Runtime\n# CACHE_MODE=on\n")
        set_env_values(self.path, {"DATABASE_USER": "other", "DATABASE_NAME": "demo", "PS_SECRET": "s", "MY_API": "x"})
        self.assertEqual(self.path.read_text(),
                         "# Copy me.\n\n# Database\n# The user only reaches its own DB.\nDATABASE_NAME=demo\n"
                         "DATABASE_USER=other\n\n# PrestaShop keys\nPS_SECRET=s\n\n# Runtime\n# CACHE_MODE=on\n\nMY_API=x\n")

    def test_a_file_without_sections_gains_them_at_the_end(self):
        self.path.write_text("DOMAIN=demo.local\nCUSTOM=1\n")
        set_env_values(self.path, {"DATABASE_ROOT_PASSWORD": "r", "OTHER": "2"})
        self.assertEqual(self.path.read_text(), "DOMAIN=demo.local\nCUSTOM=1\n\n# Database\nDATABASE_ROOT_PASSWORD=r\n\nOTHER=2\n")


if __name__ == "__main__":
    unittest.main()
