"""make shop-audit: which files of a PrestaShop shop checkout are code, release, generated, content or tool state."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'docker'))
import project_shop_audit as shop_audit  # noqa: E402


def write(root, path, text='x'):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


class Project:
    def __init__(self, directory, settings):
        self.directory = directory
        self.web_directory = directory / 'public'
        self.settings = {'PROFILE': 'prestashop', 'PS_ADMIN_DIR': 'admindev', 'DEPLOY_PROTECTED_PATHS': '', **settings}


class ShopAuditTest(unittest.TestCase):
    def shop(self, files, gitignore, tracked, settings=None):
        temporary = tempfile.TemporaryDirectory(dir=ROOT / '.test-work' if (ROOT / '.test-work').is_dir() else None)
        self.addCleanup(temporary.cleanup)
        app = Path(temporary.name) / 'app'
        shop = app / 'public'
        shop.mkdir(parents=True)
        for path in files:
            write(shop, path)
        write(shop, '.gitignore', gitignore)
        git = ['git', '-C', str(shop)]
        subprocess.run(git + ['init', '-q'], check=True)
        subprocess.run(git + ['add', '-f', '.gitignore', *tracked], check=True)
        subprocess.run(git + ['-c', 'user.email=t@example.invalid', '-c', 'user.name=t', '-c', 'commit.gpgsign=false',
                              'commit', '-qm', 'shop'], check=True)
        return Project(app, settings or {'PS_RELEASE_PATHS': 'admin vendor config/defines.inc.php'})

    def titles(self, result, kind):
        return {title.split(' (')[0].split(':')[0]: paths for title, paths in result[kind]}

    def test_code_hidden_by_broad_rules_is_an_error_and_content_or_release_is_not(self):
        project = self.shop(
            files=['classes/Cart.php', 'modules/shipping/vendor/psr/log/Psr/Log/LoggerInterface.php',
                   'modules/shipping/.htaccess', 'modules/shipping/logo.png', 'img/p/1/1.jpg', 'img/c/index.php',
                   'img/p/1/index.php', 'vendor/autoload.php', 'admindev/index.php', 'var/cache/x.php',
                   'modules/shipping/translations/lt.php', 'modules/blog/images/a.jpg', 'config/defines.inc.php'],
            gitignore='log/\n.htaccess\n*.png\n/img/\n/vendor/\n/admindev/\n/var/\n/config/defines.inc.php\n'
                      '/modules/*/translations/\n/modules/blog/images/\n',
            tracked=['classes/Cart.php'],
            settings={'PS_RELEASE_PATHS': 'admin vendor config/defines.inc.php', 'DEPLOY_PROTECTED_PATHS': 'modules/blog/images/'})
        result = shop_audit.audit(project)
        hidden = self.titles(result, 'errors')['Code hidden by the shop .gitignore']
        self.assertEqual(hidden, ['img/c/index.php', 'modules/shipping/.htaccess', 'modules/shipping/logo.png',
                                  'modules/shipping/vendor/psr/log/Psr/Log/LoggerInterface.php'])
        broad = self.titles(result, 'warnings')['.gitignore rules by file type or folder name anywhere']
        self.assertEqual(broad, ['.gitignore:1: log/', '.gitignore:2: .htaccess', '.gitignore:3: *.png'])

    def test_tracked_release_and_content_files_and_missing_core_files(self):
        project = self.shop(
            files=['admindev/index.php', 'modules/shipping/translations/lt.php', 'modules/gdpr/exports/customers.csv',
                   'classes/Cart.php'],
            gitignore='', tracked=['admindev/index.php', 'modules/shipping/translations/lt.php',
                                   'modules/gdpr/exports/customers.csv', 'classes/Cart.php'],
            settings={'PS_RELEASE_PATHS': 'admin', 'DEPLOY_PROTECTED_PATHS': 'modules/gdpr/exports/'})
        result = shop_audit.audit(project)
        errors, warnings = self.titles(result, 'errors'), self.titles(result, 'warnings')
        self.assertEqual(errors['Tracked files in PS_RELEASE_PATHS'], ['admindev/index.php'])
        self.assertIn('config/defines.inc.php', errors)
        self.assertIn('vendor', errors)
        self.assertEqual(warnings['Tracked files in protected paths'],
                         ['modules/gdpr/exports/customers.csv', 'modules/shipping/translations/lt.php'])
        self.assertEqual(warnings['Tracked files that look like dumps, keys or exports'], ['modules/gdpr/exports/customers.csv'])

    def test_a_modules_own_gitignore_is_a_warning(self):
        project = self.shop(files=['modules/shipping/vendor/phpunit/phpunit/x.php', 'modules/shipping/main.php'],
                            gitignore='', tracked=['modules/shipping/main.php'])
        write(project.web_directory, 'modules/shipping/.gitignore', '/vendor/phpunit/\n')
        result = shop_audit.audit(project)
        self.assertNotIn('Code hidden by the shop .gitignore', self.titles(result, 'errors'))
        self.assertEqual(self.titles(result, 'warnings')["Files a module's own .gitignore hides"],
                         ['modules/shipping/vendor/phpunit/phpunit/x.php'])

    def test_live_copy_splits_uncommitted_code_from_code_the_checkout_lacks(self):
        project = self.shop(files=['classes/Cart.php', 'classes/New.php'], gitignore='', tracked=['classes/Cart.php'])
        live = project.directory.parent / 'live'
        for path in ('classes/Cart.php', 'classes/New.php', 'classes/OnlyLive.php', 'adminXYZ/init.php',
                     'adminXYZ/themes/x.tpl', 'adminXYZ/filemanager/x.php', 'img/p/1.jpg', '1_lt_0_sitemap.xml'):
            write(live, path)
        result = shop_audit.audit(project, live)
        errors = self.titles(result, 'errors')
        self.assertEqual(errors['Code on the server copy that the shop checkout lacks or ignores'], ['classes/OnlyLive.php'])
        self.assertEqual(errors['Code on the server copy that is in the checkout but not committed'], ['classes/New.php'])

    def test_generic_profile_is_refused(self):
        project = self.shop(files=['index.php'], gitignore='', tracked=['index.php'])
        project.settings['PROFILE'] = ''
        with self.assertRaisesRegex(ValueError, 'PROFILE=prestashop'):
            shop_audit.audit(project)


if __name__ == '__main__':
    unittest.main()
