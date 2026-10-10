"""Services that run the application code get what php-fpm gets from setup."""

import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'docker')]
from create_project import scaffold
from project import Project


class ApplicationServicesTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / '.test-work'
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        with contextlib.redirect_stdout(io.StringIO()):
            self.app = scaffold('appenv-demo', temporary.name)

    def append(self, name, text):
        with (self.app / name).open('a') as handle:
            handle.write(text)

    def test_cron_gets_the_database_connection_and_identity_of_php_fpm(self):
        self.append('env/common.env', 'SERVICES=cron\n')
        services = Project(self.app).model()['services']
        php, cron = services['php-fpm']['environment'], services['cron']['environment']
        for key in ('DATABASE_HOST', 'DATABASE_PORT', 'DATABASE_NAME', 'DATABASE_USER', 'DATABASE_PASSWORD',
                    'DOMAIN', 'PROJECT_NAME', 'SETUP_ENV'):
            self.assertEqual(cron[key], php[key], key)
        self.assertEqual((cron['DATABASE_HOST'], cron['DATABASE_PORT']), ('database', '3306'))


if __name__ == '__main__':
    unittest.main()
