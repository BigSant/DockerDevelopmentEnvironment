"""The shop's .htaccess: written when missing at start, rewritten after a load outside prod, never for other profiles."""
import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docker"))
from project_prestashop import HTACCESS_PHP, ensure_htaccess


class HtaccessTest(unittest.TestCase):
    def setUp(self):
        (ROOT / ".test-work").mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=ROOT / ".test-work")
        self.addCleanup(temporary.cleanup)
        self.web = Path(temporary.name)
        (self.web / "index.php").write_text("<?php\n")
        self.calls = []
        self.fail_exec = False
        self.project = SimpleNamespace(settings={"PROFILE": "prestashop"}, web_directory=self.web, capture=self.capture)

    def capture(self, args):
        self.calls.append(args)
        if args[0] == "exec" and self.fail_exec:
            raise subprocess.CalledProcessError(1, args)
        return "php-fpm\n" if args[0] == "ps" else ""

    def ensure(self, **arguments):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            result = ensure_htaccess(self.project, **arguments)
        return result, output.getvalue()

    def test_written_when_missing_kept_when_present_rewritten_on_request(self):
        written, output = self.ensure()
        self.assertTrue(written)
        self.assertEqual(self.calls[-1][-1], HTACCESS_PHP)
        self.assertIn("generated", output)
        (self.web / ".htaccess").write_text("# shop\n")
        self.calls.clear()
        self.assertEqual(self.ensure(), (False, ""))
        self.assertEqual(self.calls, [])
        written, output = self.ensure(regenerate=True)
        self.assertTrue(written)
        self.assertIn("regenerated", output)

    def test_other_profiles_an_empty_checkout_and_a_stopped_php_are_left_alone(self):
        self.project.settings["PROFILE"] = ""
        self.assertEqual(self.ensure(), (False, ""))
        self.project.settings["PROFILE"] = "prestashop"
        (self.web / "index.php").unlink()
        self.assertEqual(self.ensure(), (False, ""))
        (self.web / "index.php").write_text("<?php\n")
        self.project.capture = lambda args: ""  # php-fpm is not running
        self.assertEqual(self.ensure(), (False, ""))

    def test_a_failure_warns_and_does_not_stop(self):
        self.fail_exec = True
        written, output = self.ensure()
        self.assertFalse(written)
        self.assertIn("Warning: PrestaShop could not write .htaccess", output)


if __name__ == "__main__":
    unittest.main()
