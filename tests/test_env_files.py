"""Env files read without Compose (pipeline steps, Deployment variables) and SETUP_MIN_VERSION."""

from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "docker")]
from project_env_files import parse_dotenv, require_setup_version, version_tuple


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


if __name__ == "__main__":
    unittest.main()
