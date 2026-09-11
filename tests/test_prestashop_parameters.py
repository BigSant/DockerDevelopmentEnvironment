"""Run the actual PHP startup updater against isolated shop configurations."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "docker/config/prestashop/update-parameters.php"


@unittest.skipUnless(shutil.which("php"), "PHP CLI is required")
class PrestashopParametersTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.file = self.root / "app/config/parameters.php"
        self.file.parent.mkdir(parents=True)
        self.file.write_text("<?php return ['parameters' => ['database_host' => 'old', "
                             "'database_password' => 'old-password', 'database_prefix' => 'shop_', "
                             "'cookie_key' => 'keep-key', 'secret' => 'keep-secret', "
                             "'custom' => ['nested' => true]], 'extra' => false];\n")
        self.env = os.environ.copy()
        self.env.update(PROFILE="prestashop", DATABASE_HOST="database", DATABASE_PORT="3306", DATABASE_NAME="shop",
                        DATABASE_USER="shop-user", DATABASE_PASSWORD="quote' dollar$ slash\\ percent% double%%\nline")
        for environment in ("dev", "prod"):
            cache = self.root / "var/cache" / environment
            cache.mkdir(parents=True)
            (cache / "appParameters.php").write_text("stale-credentials")
            (cache / "compiled-container.php").write_text("stale-symfony-credentials")

    def invoke(self):
        return subprocess.run(["php", str(SCRIPT), str(self.root)], env=self.env, capture_output=True, text=True)

    def load(self):
        result = subprocess.run(["php", "-r", "echo json_encode(require $argv[1]);", str(self.file)],
                                capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def test_updates_only_connections_and_clears_both_caches_without_following_links(self):
        original = self.load()
        outside = self.root / "keep-outside-cache"
        outside.mkdir()
        (outside / "keep").write_text("untouched")
        (self.root / "var/cache/prod/link").symlink_to(outside, target_is_directory=True)
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        current = self.load()
        expected = original
        for key in ("host", "port", "name", "user", "password"):
            expected["parameters"]["database_" + key] = self.env["DATABASE_" + key.upper()].replace("%", "%%")
        self.assertEqual(current, expected)
        # The application's bootstrap reverses Symfony's percent escaping.
        self.assertEqual(current["parameters"]["database_password"].replace("%%", "%"), self.env["DATABASE_PASSWORD"])
        self.assertEqual(self.file.stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.root / "var/cache/dev").exists())
        self.assertFalse((self.root / "var/cache/prod").exists())
        self.assertEqual((outside / "keep").read_text(), "untouched")
        self.assertNotIn("keep-secret", result.stdout + result.stderr)
        self.assertNotIn(self.env["DATABASE_PASSWORD"], result.stdout + result.stderr)

    def test_unchanged_restart_preserves_file_and_warmed_cache(self):
        self.assertEqual(self.invoke().returncode, 0)
        before = self.file.read_bytes(), self.file.stat().st_mtime_ns
        cache = self.root / "var/cache/prod"
        cache.mkdir()
        (cache / "warmed").write_text("keep")
        self.assertEqual(self.invoke().returncode, 0)
        self.assertEqual((self.file.read_bytes(), self.file.stat().st_mtime_ns), before)
        self.assertEqual((cache / "warmed").read_text(), "keep")

    def test_invalid_runtime_settings_leave_original_and_cache_untouched(self):
        before = self.file.read_bytes()
        for key, value in (("DATABASE_PASSWORD", ""), ("DATABASE_PORT", "70000")):
            with self.subTest(key=key):
                old = self.env[key]
                self.env[key] = value
                result = self.invoke()
                self.env[key] = old
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.file.read_bytes(), before)
                self.assertTrue((self.root / "var/cache/prod/appParameters.php").exists())

    def test_missing_or_invalid_shop_configuration_fails_without_generating_keys(self):
        for contents in (None, "<?php return [];", "<?php invalid syntax keep-secret"):
            with self.subTest(contents=contents):
                if contents is None:
                    self.file.unlink()
                else:
                    self.file.write_text(contents)
                result = self.invoke()
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("keep-secret", result.stdout + result.stderr)
                self.assertEqual(self.file.read_text() if self.file.exists() else None, contents)

    def test_symlinked_cache_root_is_rejected_before_config_write(self):
        before = self.file.read_bytes()
        cache = self.root / "var/cache/prod"
        shutil.rmtree(cache)
        outside = self.root / "unrelated"
        outside.mkdir()
        cache.symlink_to(outside, target_is_directory=True)
        self.assertNotEqual(self.invoke().returncode, 0)
        self.assertEqual(self.file.read_bytes(), before)
        self.assertTrue(outside.is_dir())


if __name__ == "__main__":
    unittest.main()
