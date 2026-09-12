"""Minimal projects omit missing shared config; explicit mounts remain required."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'docker')]
from create_project import scaffold
from project import Project
from project_policy import resolve_policy


class OptionalMountsTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / '.test-work'
        scratch.mkdir(exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temp.cleanup)
        with contextlib.redirect_stdout(io.StringIO()):
            self.app = scaffold('optional-config', temp.name)

    def test_missing_defaults_are_omitted_and_added_directories_are_detected(self):
        first = Project(self.app)
        for service in first.model()['services'].values():
            for volume in service.get('volumes', []):
                self.assertFalse(volume.get('source', '').startswith(str(self.app / 'config')))
        self.assertFalse((self.app / 'config').exists())
        folder = self.app / 'config/php/local'
        folder.mkdir(parents=True)
        (folder / 'custom.ini').write_text('max_input_vars=25000\n')
        second = Project(self.app)
        mounts = {v['target']:v['source'] for v in second.model()['services']['php-fpm']['volumes']}
        self.assertEqual(Path(mounts['/usr/local/etc/php/project.d.env']), folder)
        self.assertEqual(Path(mounts['/usr/local/etc/php/project.d']), folder.parent)
        self.assertEqual(Path(mounts['/opt/setup/project']), folder.parent.parent)
        # An already constructed command keeps its independent generated overlay.
        self.assertNotIn('/opt/setup/project', [v['target'] for v in first.model()['services']['php-fpm']['volumes']])

    def test_explicit_missing_sources_are_not_silently_removed(self):
        (self.app / 'compose/local.yaml').write_text('''services:
  php-fpm:
    volumes:
      - ./config/custom-php:/usr/local/etc/php/project.d:ro
      - ./config/required.ini:/usr/local/etc/php/conf.d/required.ini:ro
''')
        mounts = {v['target']:v['source'] for v in Project(self.app).model()['services']['php-fpm']['volumes']}
        self.assertEqual(mounts['/usr/local/etc/php/project.d'], str(self.app / 'config/custom-php'))
        self.assertIn('/usr/local/etc/php/conf.d/required.ini', mounts)
        self.assertNotIn('/usr/local/etc/php/project.d.env', mounts)

    def test_fpm_ranges_and_pool_relationships(self):
        defaults = resolve_policy({}, 'local')
        self.assertEqual(defaults['PHP_FPM_MAX_CHILDREN'], '4')
        for settings in ({'PHP_FPM_MAX_CHILDREN':'0'}, {'PHP_FPM_MAX_REQUESTS':'-1'},
                         {'PHP_FPM_START_SERVERS':'abc'}, {'PHP_FPM_START_SERVERS':'3'},
                         {'PHP_FPM_MAX_CHILDREN':'1'}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                resolve_policy(settings, 'local')
        self.assertEqual(resolve_policy({'PHP_FPM_MAX_REQUESTS':'0'}, 'prod')['PHP_FPM_MAX_REQUESTS'], '0')
