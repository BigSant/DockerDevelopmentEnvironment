"""The Database client against a stand-in for `docker compose`: a shell script that records what it was given."""

import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "docker"), str(Path(__file__).resolve().parent)]
from database_client import DUMP, IMPORT, READ, READ_RAW, DatabaseClient, DatabaseError
from fake_database import FakeDatabase


class DatabaseClientTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.log = Path(temporary.name)
        self.running = "database\nphp-fpm\n"

    def client(self, body, database="shop"):
        """A client whose Compose command runs body; $7 is the client script, $8 its label, $9 the database."""
        script = f'printf "%s\\0" "$@" > "{self.log}/argv"\n{body}'
        project = SimpleNamespace(settings={"DATABASE_NAME": database}, environment="local",
                                  command=["sh", "-c", script, "compose"], compose_env=lambda args: dict(os.environ),
                                  capture=lambda args: self.running)
        return DatabaseClient(project)

    def argv(self):
        return (self.log / "argv").read_text().split("\0")[:-1]

    def test_query_returns_unescaped_rows_and_reads_as_the_application_user(self):
        client = self.client(r"cat > /dev/null; printf 'a\tb\\tc\n\nd\\\\e\tf\n'")
        self.assertEqual(client.query("SELECT 1;\n"), [("a", "b\tc"), ("d\\e", "f")])
        self.assertEqual(self.argv()[:8], ["exec", "-T", "database", "sh", "-c", READ, "database-read", "shop"])

    def test_extract_returns_raw_bytes(self):
        client = self.client(r"cat > /dev/null; printf 'a\tb\nc\0'")
        self.assertEqual(client.extract("SELECT 1;\n"), b"a\tb\nc\0")
        self.assertEqual(self.argv()[5], READ_RAW)

    def test_execute_sends_the_sql_on_stdin(self):
        client = self.client(f'cat > "{self.log}/stdin"')
        self.assertIsNone(client.execute("CREATE TABLE t (id int);\nINSERT INTO t VALUES (1);\n"))
        self.assertEqual((self.log / "stdin").read_text(), "CREATE TABLE t (id int);\nINSERT INTO t VALUES (1);\n")
        self.assertEqual(self.argv()[5:7], [READ, "database-write"])

    def test_a_failure_shows_the_client_error_line_and_never_compose_output(self):
        client = self.client("cat > /dev/null; echo 'compose: DATABASE_PASSWORD=s3cret' >&2; "
                             "echo 'ERROR 1064 (42000) at line 2: You have an error in your SQL syntax' >&2; exit 1")
        with self.assertRaises(DatabaseError) as raised:
            client.execute("BROKEN;\n")
        self.assertEqual(str(raised.exception),
                         "Database write failed: ERROR 1064 (42000) at line 2: You have an error in your SQL syntax")
        self.assertIsInstance(raised.exception, ValueError)

    def test_without_a_client_line_it_tells_a_stopped_database_from_other_failures(self):
        client = self.client("cat > /dev/null; echo 'service \"database\" is not running' >&2; exit 1")
        self.running = "php-fpm\n"
        with self.assertRaisesRegex(DatabaseError, r"^Cannot read the database: the database service is not running; "
                                                   r"start it with make up$"):
            client.query("SELECT 1;\n")
        self.running = "database\n"
        with self.assertRaisesRegex(DatabaseError, r"^Cannot read the database; check the database service"):
            client.query("SELECT 1;\n")

    def test_another_database_name_in_the_container_is_named(self):
        client = self.client("cat > /dev/null; exit 64")
        with self.assertRaisesRegex(DatabaseError, "belongs to another DATABASE_NAME"):
            client.query("SELECT 1;\n")

    def test_a_read_that_does_not_answer_times_out(self):
        client = self.client("cat > /dev/null; sleep 5")
        with self.assertRaisesRegex(DatabaseError, r"^Cannot read the database: no answer within 0.2 s$"):
            client.query("SELECT 1;\n", timeout=0.2)

    def test_database_name_is_required(self):
        with self.assertRaisesRegex(DatabaseError, "DATABASE_NAME is required"):
            self.client("true", database="").query("SELECT 1;\n")

    def test_load_streams_into_the_root_client(self):
        client = self.client(f'cat > "{self.log}/stdin"')
        with client.load() as sink:
            sink.write(b"INSERT INTO t VALUES (1);\n")
            sink.write(b"INSERT INTO t VALUES (2);\n")
        self.assertEqual((self.log / "stdin").read_bytes(), b"INSERT INTO t VALUES (1);\nINSERT INTO t VALUES (2);\n")
        self.assertEqual(self.argv()[5:7], [IMPORT, "database-load"])

    def test_a_client_that_stops_early_fails_the_load_with_its_error(self):
        client = self.client("echo 'ERROR 1146 (42S02) at line 1: Table missing' >&2; exit 1")
        with self.assertRaisesRegex(DatabaseError, r"^Database load failed: ERROR 1146"):
            with client.load() as sink:
                for _ in range(64):
                    sink.write(b"x" * 65536)

    def test_dump_streams_the_output_and_names_an_extra_schema(self):
        client = self.client("printf 'CREATE TABLE t;\\n'")
        with client.dump("--no-data", "t") as stream:
            self.assertEqual(stream.read(), b"CREATE TABLE t;\n")
        self.assertEqual(self.argv()[5:], [DUMP, "database-dump", "shop", "--no-data", "t"])
        with client.dump("--no-data", schema="shop_extra") as stream:
            stream.read()
        self.assertEqual(self.argv()[7:], ["shop", "--schema", "shop_extra", "--no-data"])
        with client.dump(schema="shop") as stream:
            stream.read()
        self.assertEqual(self.argv()[7:], ["shop"])

    def test_a_failing_dump_raises_its_error_line(self):
        client = self.client("printf 'partial'; echo 'mysqldump: Got error: 1044: Access denied' >&2; exit 2")
        with self.assertRaisesRegex(DatabaseError, r"^Database dump failed: mysqldump: Got error: 1044"):
            with client.dump() as stream:
                stream.read()

    def test_a_dump_left_by_an_error_is_stopped(self):
        client = self.client("while :; do echo row; done")
        with self.assertRaises(RuntimeError):
            with client.dump() as stream:
                stream.read(10)
                raise RuntimeError("caller failed")


class FakeDatabaseTest(unittest.TestCase):
    def test_it_answers_records_and_fails_on_request(self):
        applied = {"001.sql"}
        database = FakeDatabase(answer=lambda sql: [(name,) for name in sorted(applied)],
                                fail_on=lambda operation, text: operation == "write" and "BROKEN" in text)
        self.assertEqual(database.query("SELECT name FROM setup_migrations;\n"), [("001.sql",)])
        database.execute("CREATE TABLE t (id int);\n")
        with self.assertRaises(DatabaseError):
            database.execute("BROKEN;\n")
        with database.load() as sink:
            sink.write(b"INSERT;\n")
        self.assertEqual(database.executed, ["CREATE TABLE t (id int);\n", "BROKEN;\n"])
        self.assertEqual(database.loaded, [b"INSERT;\n"])


if __name__ == "__main__":
    unittest.main()
