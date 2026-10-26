"""Translation table rows written by make translations-apply's SQL on a disposable MySQL/MariaDB container."""

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docker"))
from database_client import unescape_batch  # noqa: E402
from project_translations import apply_translations, export_translations  # noqa: E402

IMAGES = [image for image in (os.environ.get("SETUP_MYSQL_IMAGE"), os.environ.get("SETUP_MARIADB_IMAGE")) if image]
PASSWORD = "translations-test-only"
# PrestaShop 8's two tables, as its install creates them.
SCHEMA = """CREATE TABLE ps_lang (id_lang int unsigned NOT NULL AUTO_INCREMENT, name varchar(32) NOT NULL, active tinyint(1) NOT NULL,
  iso_code char(2) NOT NULL, language_code char(5) NOT NULL, locale varchar(5) NOT NULL, PRIMARY KEY (id_lang))
  ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE ps_translation (id_translation int NOT NULL AUTO_INCREMENT, id_lang int NOT NULL, `key` text NOT NULL,
  translation text NOT NULL, domain varchar(80) NOT NULL, theme varchar(32) DEFAULT NULL, PRIMARY KEY (id_translation),
  KEY id_lang (id_lang), KEY `key` (domain)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
INSERT INTO ps_lang (name, active, iso_code, language_code, locale) VALUES ('Lietuvių', 1, 'lt', 'lt-lt', 'lt-LT'),
  ('English', 1, 'en', 'en-us', 'en-US');
INSERT INTO ps_translation (id_lang, `key`, translation, domain, theme) VALUES
  (1, 'Add to cart', 'Kliento „krepšelis“', 'Modules.Shop.Shop', NULL),
  (1, 'Checkout', 'Kliento apmokėjimas', 'Modules.Shop.Shop', NULL),
  (1, 'Other', 'Kito domeno', 'Modules.Shop_x.Shop', NULL);
"""


class Database:
    """The DatabaseClient's query/execute over docker exec: the same batch output and unescaping."""

    def __init__(self, test, name):
        self.test, self.name = test, name

    def run(self, sql):
        result = subprocess.run(["docker", "exec", "-i", "-e", f"MYSQL_PWD={PASSWORD}", self.name, "sh", "-c",
                                 'c=$(command -v mariadb || command -v mysql); exec "$c" -h127.0.0.1 -uroot -N -B shop'],
                                input=sql, capture_output=True, text=True)
        self.test.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def query(self, sql, timeout=60):
        return [tuple(unescape_batch(field) for field in line.split("\t")) for line in self.run(sql).split("\n") if line]

    def execute(self, sql, timeout=None):
        self.run(sql)


@unittest.skipUnless(IMAGES, "Set SETUP_MYSQL_IMAGE and/or SETUP_MARIADB_IMAGE to built setup database images")
class TranslationsIntegrationTest(unittest.TestCase):
    def start(self, image):
        name = f"setup-translations-{uuid.uuid4().hex[:8]}"
        subprocess.run(["docker", "run", "-d", "--rm", "--name", name, "--network", "none", "--tmpfs", "/var/lib/mysql",
                        "-e", f"MYSQL_ROOT_PASSWORD={PASSWORD}", "-e", f"MARIADB_ROOT_PASSWORD={PASSWORD}",
                        "-e", "MYSQL_DATABASE=shop", "-e", "MARIADB_DATABASE=shop",
                        "-e", "DATABASE_BUFFER_POOL=128M", "-e", "DATABASE_PERFORMANCE_SCHEMA=off", image], check=True, capture_output=True)
        self.addCleanup(subprocess.run, ["docker", "rm", "-f", name], capture_output=True)
        deadline = time.time() + 120
        while time.time() < deadline:
            probe = subprocess.run(["docker", "exec", "-e", f"MYSQL_PWD={PASSWORD}", name, "sh", "-c",
                                    'c=$(command -v mariadb || command -v mysql); exec "$c" -h127.0.0.1 -uroot shop -e "SELECT 1"'],
                                   capture_output=True)
            if probe.returncode == 0:
                return Database(self, name)
            time.sleep(2)
        self.fail("database did not start")

    def project(self, database, root, environment="prod"):
        return SimpleNamespace(environment=environment, root=root, directory=root / "app", web_directory=root / "app/public",
                               settings={"PROFILE": "prestashop", "PS_VERSION": "8.2.0", "DEPLOY_PATH": str(root / "target"),
                                         "DEPLOY_SSH": "", "DEPLOY_CONTEXT": "default", "APP_UID": "1000", "APP_GID": "1000"},
                               database=database)

    def test_rows_are_updated_inserted_and_recorded_and_an_unchanged_one_keeps_the_clients_edit(self):
        for image in IMAGES:
            with self.subTest(image=image), tempfile.TemporaryDirectory() as directory:
                database = self.start(image)
                database.execute(SCHEMA)
                root = Path(directory)
                repository = root / "app/translations/database/lt-LT"
                repository.mkdir(parents=True)
                texts = {"Add to cart": "Į „krepšelį“ – it's \\ ok", "Checkout": "Apmokėti", "New": "Naujas"}
                (repository / "Modules.Shop.Shop.json").write_text(json.dumps(
                    {"locale": "lt-LT", "domain": "Modules.Shop.Shop", "theme": None, "translations": texts}))
                project = self.project(database, root)
                with contextlib.redirect_stdout(io.StringIO()):
                    first = apply_translations(project)
                self.assertEqual((first.written, len(first.over)), (3, 2))
                rows = dict(database.query("SELECT `key`, translation FROM ps_translation WHERE domain = 'Modules.Shop.Shop' "
                                           "AND theme IS NULL;"))
                self.assertEqual(rows, texts)
                # The client edits Checkout; the repository changes only Add to cart.
                database.execute("UPDATE ps_translation SET translation = 'Kliento' WHERE `key` = 'Checkout';")
                texts["Add to cart"] = "Į krepšelį"
                (repository / "Modules.Shop.Shop.json").write_text(json.dumps(
                    {"locale": "lt-LT", "domain": "Modules.Shop.Shop", "theme": None, "translations": texts}))
                with contextlib.redirect_stdout(io.StringIO()):
                    second = apply_translations(project)
                self.assertEqual((second.written, second.kept), (1, 2))
                rows = dict(database.query("SELECT `key`, translation FROM ps_translation WHERE domain = 'Modules.Shop.Shop';"))
                self.assertEqual(rows, {"Add to cart": "Į krepšelį", "Checkout": "Kliento", "New": "Naujas"})
                self.assertEqual(database.query("SELECT COUNT(*) FROM setup_translations;"), [("3",)])
                # The export's LIKE keeps _ literal: Modules.Shop_x is not Modules.Shopx.
                with contextlib.redirect_stdout(io.StringIO()):
                    _, exported = export_translations(self.project(database, root, "local"), [], ["Modules.Shop_x.*"], [])
                self.assertEqual(exported, {("lt-LT", "Modules.Shop_x.Shop", ""): {"Other": "Kito domeno"}})


if __name__ == "__main__":
    unittest.main()
