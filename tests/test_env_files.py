"""Env files read without Compose (pipeline steps, Deployment variables) and SETUP_MIN_VERSION."""

from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "docker")]
from project import env_file_keys
from project_env_files import hides_process_value, parse_dotenv, require_setup_version, version_tuple


class EnvFileTest(unittest.TestCase):
    def test_dotenv_rules(self):
        values = parse_dotenv(textwrap.dedent("""\
            # comment
            PLAIN=value   # inline comment
            export EXPORTED=1
            SINGLE='a $literal # not a comment'
            DOUBLE="line\\nnext \\"quoted\\""
            MULTI="
              ONE TWO
            "
            EMPTY=
            COMMENTED= # nothing
            """))
        self.assertEqual(values, {"PLAIN": "value", "EXPORTED": "1", "SINGLE": "a $literal # not a comment",
                                  "DOUBLE": 'line\nnext "quoted"', "MULTI": "\n  ONE TWO\n", "EMPTY": "", "COMMENTED": ""})
        with self.assertRaisesRegex(ValueError, "x:1: KEY has no closing single quote"):
            parse_dotenv("KEY='open\n", "x")
        with self.assertRaisesRegex(ValueError, r"x:1: KEY holds \$ outside single quotes"):
            parse_dotenv('KEY="a$b"\n', "x", strict=True)
        with self.assertRaisesRegex(ValueError, "x:2: not a KEY=value line"):
            parse_dotenv("A=1\nsecret-VALUE\n", "x")

    def test_setup_min_version(self):
        self.assertLess(version_tuple("2.2.0"), version_tuple("2.10"))
        self.assertIsNone(version_tuple("2.x"))
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as name:
            directory = Path(name)
            (directory / "env").mkdir()
            current = (ROOT / "VERSION").read_text().strip()
            for wanted in ("", current, "v2.0", "1"):
                (directory / "env/common.env").write_text(f"SETUP_MIN_VERSION={wanted}\n")
                require_setup_version(directory)
            (directory / "env/common.env").write_text("SETUP_MIN_VERSION=99.1\n")
            with self.assertRaisesRegex(ValueError, rf"needs setup 99.1 or newer .*is {current}"):
                require_setup_version(directory)
            (directory / "env/common.env").write_text("SETUP_MIN_VERSION=latest\n")
            with self.assertRaisesRegex(ValueError, "must be a version such as"):
                require_setup_version(directory)
            # Every runner command checks it, before anything else.
            (directory / "env/common.env").write_text("PROJECT_NAME=x\nSETUP_MIN_VERSION=99.1\n")
            result = subprocess.run([sys.executable, str(ROOT / "docker/project.py"), "--docker-directory", str(directory),
                                     "check"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn("This project needs setup 99.1 or newer", result.stderr)


class ProcessEnvironmentTest(unittest.TestCase):
    """Which process variables the env files hide (Project drops them; deployment_variables lists the rest)."""

    def test_a_named_key_hides_the_process_value_unless_it_is_a_blank_setup_secret(self):
        self.assertTrue(hides_process_value("DOMAIN", "shop.local"))
        self.assertTrue(hides_process_value("DOMAIN", ""), "a blank non-secret line hides the process value too")
        self.assertTrue(hides_process_value("DATABASE_PASSWORD", "from-the-file"))
        self.assertFalse(hides_process_value("DATABASE_PASSWORD", ""), "the pipeline supplies a blank secret")
        self.assertFalse(hides_process_value("PMA_HTPASSWD", ""))

    def test_lines_inside_a_quoted_value_are_not_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / "prod.env"
            env.write_text('KEY_PEM="-----BEGIN-----\nDATABASE_PASSWORD=not-a-key\n-----END-----"\n'
                           'DATABASE_ROOT_PASSWORD=""\nexport TOKEN=x\n')
            self.assertEqual(env_file_keys([env]), {"KEY_PEM": "-----BEGIN-----\nDATABASE_PASSWORD=not-a-key\n-----END-----",
                                                    "DATABASE_ROOT_PASSWORD": "", "TOKEN": "x"})
            env.write_text("PLAIN=1\nnot a setting\n")
            with self.assertRaisesRegex(ValueError, "prod.env:2: not a KEY=value line"):
                env_file_keys([env])


class ImportTest(unittest.TestCase):
    def test_the_deployment_variables_users_do_not_load_the_ci_step(self):
        """A stage/prod Project (secret store) and make deploy-variables need the list, not the pipeline step."""
        code = ("import sys; import project_secret_store, project_bitbucket, project_github; "
                "print('project_pipeline' in sys.modules)")
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT / "docker", capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), "False")


if __name__ == "__main__":
    unittest.main()
