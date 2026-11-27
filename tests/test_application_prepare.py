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
from project_application import plan_application, prepare_application, release_into_test, seed_command, seeded_directories


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
        self.assertEqual(len(commands), 2 + len(seeded_directories()), 'the listed paths, then the protected directories')
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
        self.assertTrue(composer)
        self.assertFalse([command for command in commands if 'release/9.0.2/vendor ' in command], 'composer.lock defines vendor/')
        with patch.object(Project, 'run') as run, contextlib.redirect_stdout(io.StringIO()):
            prepare_application(project)
        self.assertEqual((self.app / 'public/composer.lock').read_text(), '{}\n')
        self.assertEqual(run.call_count, 2, 'the release files of the protected directories, then composer')
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

    def test_protected_directories_take_only_the_release_files_the_shop_lacks(self):
        self.assertEqual(seeded_directories(), ['var', 'img', 'upload', 'download', 'cache', 'config/themes',
                                                'app/Resources/translations', 'translations/export'])
        self.assertEqual(plan_application(Project(self.app)), ([], False), 'PS_RELEASE off: nothing from a release')
        self.common('PS_RELEASE=on\n')
        commands, _ = plan_application(Project(self.app))
        self.assertEqual(commands, [seed_command('9.0.2', directory) for directory in seeded_directories()])
        self.assertIn('--skip-old-files', commands[0])
        # The command itself: the release's missing files arrive, the shop's own stay, no release directory is no error.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, text in {'release/9.0.2/cache/.htaccess': 'Deny from all\n', 'release/9.0.2/cache/index.php': 'release\n',
                               'release/9.0.2/cache/smarty/compile/index.php': 'release\n', 'shop/cache/index.php': 'shop\n',
                               'shop/cache/smarty/compile/page.tpl.php': 'compiled\n'}.items():
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text(text)
            for name in ('cache', 'upload'):
                subprocess.run(['sh', '-c', seed_command('9.0.2', name, root / 'release', root / 'shop')], check=True,
                               capture_output=True)
            self.assertEqual((root / 'shop/cache/.htaccess').read_text(), 'Deny from all\n')
            self.assertEqual((root / 'shop/cache/index.php').read_text(), 'shop\n', 'a file the shop has is never replaced')
            self.assertEqual((root / 'shop/cache/smarty/compile/index.php').read_text(), 'release\n')
            self.assertEqual((root / 'shop/cache/smarty/compile/page.tpl.php').read_text(), 'compiled\n')
            self.assertFalse((root / 'shop/upload').exists(), 'a directory the release lacks is left alone')

    def test_paths_require_release_on(self):
        self.common('PS_RELEASE_PATHS=vendor\n')
        with self.assertRaisesRegex(ValueError, 'requires PS_RELEASE=on'):
            plan_application(Project(self.app))


class SessionReleaseTest(unittest.TestCase):
    """A test session of a PrestaShop shop whose repository lacks the release paths (they are ignored in its .gitignore,
    like a shop built from the official release): make test-prepare completes the ENV=test copy from the release."""
    PATHS = 'vendor bin config/defines.inc.php themes/classic composer.json'

    def setUp(self):
        scratch = ROOT / '.test-work'
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        with contextlib.redirect_stdout(io.StringIO()):
            self.app = scaffold('release-session', temporary.name)
        content = self.root / 'content'
        for name in ('tests', 'config', 'public'):
            (content / name).mkdir(parents=True)
        # The caller's shop code: its own code, no release path but one it planted in place of the release's.
        shop = content / 'public'
        (shop / 'index.php').write_text('<?php // the shop\n')
        (shop / 'modules/custom').mkdir(parents=True)
        (shop / 'modules/custom/custom.php').write_text('<?php // a module of the shop\n')
        (shop / 'config').mkdir()
        (shop / 'config/defines.inc.php').write_text('<?php // planted by the caller\n')
        (shop / '.gitignore').write_text(''.join(f'/{path}/\n' for path in self.PATHS.split()))
        session = (f'TEST_SESSION=on\nE2E_TESTS_DIRECTORY={content}/tests\nE2E_CONFIG_DIRECTORY={content}/config\n'
                   f'TEST_APP_SOURCE={shop}\nTEST_SESSION_ID=ts-0123456789ab\n')
        with (self.app / 'env/common.env').open('a') as env:
            env.write(f'PROFILE=prestashop\nPS_VERSION=8.2.1\nPHP_VERSION=8.1\nPS_RELEASE=on\nPS_RELEASE_PATHS={self.PATHS}\n')
        (self.app / 'env/local.env').write_text('DOMAIN=shop.localhost\nDATABASE_NAME=shop\nDATABASE_USER=local\n'
                                                'DATABASE_ROOT_PASSWORD=r\nDATABASE_PASSWORD=p\nLOCALHOST_PORT=32001\n'
                                                'LOCALHOST_PORT_SSL=32002\n' + session)
        (self.app / 'env/test.env').write_text('TEST_TLS=self-signed\nTEST_HOST_ROUTE=off\n' + session)

    def prepare(self):
        """make test-prepare with Docker replaced: the order of its steps and the compose calls."""
        from project_testing import prepare_tests
        steps = []
        def run(project, arguments, *rest, **keywords):
            steps.append((project.environment, arguments))
            return subprocess.CompletedProcess(arguments, 0)
        with patch.object(Project, 'run', run), patch('project_bootstrap.prepare_host'), \
                patch('database_load.plan_load'), patch('database_load.run_load', lambda *a: steps.append(('load', None))), \
                patch('project_health.start_project', lambda *a: steps.append(('start', None))), \
                contextlib.redirect_stdout(io.StringIO()):
            prepare_tests(Project(self.app))
        return steps

    def test_the_test_copy_gets_the_release_paths_before_it_starts(self):
        steps = self.prepare()
        release = [arguments for environment, arguments in steps if arguments and 'php-fpm' in arguments]
        self.assertEqual(len(release), 1, steps)
        self.assertEqual(release[0][:7], ['run', '--rm', '--no-deps', '--entrypoint', 'sh', 'php-fpm', '-c'])
        environments = [environment for environment, arguments in steps if arguments and 'php-fpm' in arguments]
        self.assertEqual(environments, ['test'], 'in ENV=test, on its copy, never in ENV=local')
        self.assertLess(steps.index(('test', release[0])), steps.index(('start', None)), 'before ENV=test starts')
        # The command itself, on a release and the session's copy: every listed path comes from the release, the
        # caller's planted defines.inc.php is replaced, its own code stays.
        releases = self.root / 'release'
        for name in ('vendor/autoload.php', 'bin/console', 'config/defines.inc.php', 'themes/classic/theme.yml',
                     'composer.json', 'img/p/index.php'):
            (releases / '8.2.1' / name).parent.mkdir(parents=True, exist_ok=True)
            (releases / '8.2.1' / name).write_text(f'release {name}\n')
        copy = self.app / '.generated/test/app'
        self.assertFalse((copy / 'vendor').exists(), 'the shop repository has no release path')
        script = release[0][7].replace('/opt/prestashop/release', str(releases)).replace('/var/www/html', str(copy))
        subprocess.run(['sh', '-c', script], check=True, capture_output=True)
        for name in ('vendor/autoload.php', 'bin/console', 'config/defines.inc.php', 'themes/classic/theme.yml', 'composer.json'):
            self.assertEqual((copy / name).read_text(), f'release {name}\n', name)
        self.assertEqual((copy / 'img/p/index.php').read_text(), 'release img/p/index.php\n')
        self.assertEqual((copy / 'modules/custom/custom.php').read_text(), '<?php // a module of the shop\n')
        self.assertEqual((copy / 'index.php').read_text(), '<?php // the shop\n')
        # A path missing from the release stops the session rather than leaving the copy half made.
        (releases / '8.2.1/bin/console').unlink(); (releases / '8.2.1/bin').rmdir()
        self.assertNotEqual(subprocess.run(['sh', '-c', script], capture_output=True).returncode, 0)

    def test_the_tracked_files_of_the_trusted_tree_do_not_matter_and_local_is_left_alone(self):
        web = self.app / 'public'
        (web / 'vendor').mkdir(parents=True, exist_ok=True)
        (web / 'vendor/tracked.php').write_text('<?php\n')
        subprocess.run(['git', 'init', '-q', str(web)], check=True)
        subprocess.run(['git', '-C', str(web), 'add', 'vendor/tracked.php'], check=True)
        with patch.object(Project, 'run') as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(release_into_test(Project(self.app), Project(self.app)))
        self.assertEqual(run.call_count, 1)
        # Without TEST_SESSION=on (a developer's ENV=test) make test-prepare copies the developer's prepared shop.
        for name in ('local.env', 'test.env'):
            path = self.app / 'env' / name
            path.write_text(path.read_text().replace('TEST_SESSION=on', 'TEST_SESSION=off'))
        with patch.object(Project, 'run') as run:
            self.assertFalse(release_into_test(Project(self.app), Project(self.app)))
        run.assert_not_called()

    def test_a_composer_project_is_refused_plainly(self):
        (self.app / 'config/composer').mkdir(parents=True)
        (self.app / 'config/composer/composer.json').write_text('{}\n')
        with patch.object(Project, 'run') as run, self.assertRaisesRegex(ValueError, 'needs the network'):
            release_into_test(Project(self.app), Project(self.app))
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
