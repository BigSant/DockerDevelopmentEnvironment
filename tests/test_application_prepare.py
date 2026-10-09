"""The application preparation step: release paths, composer files and the release image source."""
import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'docker')]
from create_project import scaffold
from project import Project
from project_application import plan_application, prepare_application


class ApplicationPrepareTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / '.test-work'
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        with contextlib.redirect_stdout(io.StringIO()):
            self.app = scaffold('release-demo', temporary.name)
        self.common('PROFILE=prestashop\nPS_VERSION=9.0.2\nPHP_VERSION=8.4\n')

    def common(self, text):
        with (self.app / 'env/common.env').open('a') as env:
            env.write(text)

    def test_release_image_follows_ps_release_and_version(self):
        args = lambda: Project(self.app).model()['services']['php-fpm-core']['build']['args']
        self.assertTrue(args()['PS_RELEASE_IMAGE'].startswith('php-fpm-base-local-8.4-'))
        self.common('PS_RELEASE=on\n')
        self.assertEqual(args()['PS_RELEASE_IMAGE'], 'prestashop/prestashop:9.0.2')
        self.assertEqual(args()['PS_RELEASE'], 'on')
        self.common('PS_VERSION=1.6.1.24\nPHP_VERSION=7.1\n')
        # 1.6 has no official image: the build downloads the release zip instead.
        self.assertTrue(args()['PS_RELEASE_IMAGE'].startswith('php-fpm-base-local-7.1-'))

    def test_only_listed_paths_are_copied_and_admin_is_renamed(self):
        self.common('PS_RELEASE=on\nPS_RELEASE_PATHS=vendor admin\nPS_ADMIN_DIR=admin123\n')
        commands, composer = plan_application(Project(self.app))
        self.assertFalse(composer)
        self.assertEqual(len(commands), 2)
        self.assertIn('/opt/prestashop/release/9.0.2/vendor /var/www/html/vendor', commands[0])
        self.assertIn('/opt/prestashop/release/9.0.2/admin /var/www/html/admin123', commands[1])
        self.assertIn('rm -rf /var/www/html/admin123', commands[1])

    def test_project_composer_files_replace_the_release_vendor(self):
        self.common('PS_RELEASE=on\nPS_RELEASE_PATHS=vendor\n')
        (self.app / 'config/composer').mkdir(parents=True)
        (self.app / 'config/composer/composer.json').write_text('{}\n')
        (self.app / 'config/composer/composer.lock').write_text('{}\n')
        project = Project(self.app)
        commands, composer = plan_application(project)
        self.assertEqual((commands, composer), ([], True))
        with patch.object(Project, 'run') as run, contextlib.redirect_stdout(io.StringIO()):
            prepare_application(project)
        self.assertEqual((self.app / 'public/composer.lock').read_text(), '{}\n')
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0][:7], ['run', '--rm', '--no-deps', '--entrypoint', 'composer', 'php-fpm', 'install'])
        self.assertNotIn('--no-dev', run.call_args.args[0])

    def test_unsafe_paths_installer_and_tracked_code_are_refused(self):
        cases = {'PS_RELEASE_PATHS=install\n': 'installer', 'PS_RELEASE_PATHS=vendor;rm\n': 'plain relative path'}
        for text, message in cases.items():
            with self.subTest(text=text):
                self.common(f'PS_RELEASE=on\n{text}')
                with self.assertRaisesRegex(ValueError, message):
                    plan_application(Project(self.app))
        self.common('PS_RELEASE_PATHS=../outside\n')
        with self.assertRaisesRegex(ValueError, 'relative paths inside the shop'):
            Project(self.app)
        self.common('PS_RELEASE_PATHS=classes\n')
        web = self.app / 'public'
        (web / 'classes').mkdir()
        (web / 'classes/Tools.php').write_text('<?php\n')
        subprocess.run(['git', 'init', '-q', str(web)], check=True)
        subprocess.run(['git', '-C', str(web), 'add', 'classes/Tools.php'], check=True)
        with self.assertRaisesRegex(ValueError, 'tracked by the shop repository'):
            plan_application(Project(self.app))

    def test_paths_require_release_on(self):
        self.common('PS_RELEASE_PATHS=vendor\n')
        with self.assertRaisesRegex(ValueError, 'requires PS_RELEASE=on'):
            plan_application(Project(self.app))


if __name__ == '__main__':
    unittest.main()
