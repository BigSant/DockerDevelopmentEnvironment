"""The schema pre-commit hook in a linked worktree, run by real `git commit` with a fake DB."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "docker"), str(Path(__file__).resolve().parent)]
from database_schema import export_schema, git, in_committing_checkout, schema_directory
from test_database_schema import snapshot

PRODUCT = "CREATE TABLE `product` (\n  `id` int NOT NULL\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n"
FEED = "CREATE TABLE `feed` (\n  `id` int NOT NULL,\n  PRIMARY KEY (`id`)\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n"

# The installed hook runs project.py with the main checkout's settings; this one runs the same
# schema code with the DB read stubbed from state.json.
HOOK = textwrap.dedent("""\
    import json, sys
    from pathlib import Path
    from types import SimpleNamespace
    sys.path[:0] = [{docker!r}, {tests!r}]
    import database_schema
    from test_database_schema import snapshot
    state = json.loads(Path({state!r}).read_text())
    database_schema.database_tables = lambda project: sorted(state["tables"])
    database_schema.read_schema = lambda project, require_classified=False: snapshot(state["tables"])
    project = SimpleNamespace(root=Path(state["root"]), directory=Path(state["root"]) / "docker", environment="local",
                              settings={{"SCHEMA_DIRECTORY": "database/schema", "DATABASE_NAME": "shop"}},
                              command=["false"], child_env=dict)
    try:
        getattr(database_schema, state.get("action", "commit_schema"))(project)
    except ValueError as error:
        print(error)
        sys.exit(1)
""")


def tree(directory):
    """Every file under directory with its bytes, to prove a checkout was left alone."""
    return {path.relative_to(directory).as_posix(): path.read_bytes()
            for path in sorted(directory.rglob("*")) if path.is_file() and ".git" not in path.parts}


class WorktreeHookTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="schema worktree ", dir=scratch)
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.main = base / "main"
        self.worktree = base / "linked"
        self.main.mkdir()
        git(self.main, "init", "-q")
        for key, value in (("user.name", "Test"), ("user.email", "test@example.invalid"), ("commit.gpgsign", "false")):
            git(self.main, "config", key, value)
        registry = self.main / "database/table-registry.txt"
        registry.parent.mkdir(parents=True)
        registry.write_text("product catalog slice import\nfeed config full import\n")
        project = type("Project", (), {"root": self.main, "settings": {"SCHEMA_DIRECTORY": "database/schema"}})
        with open(os.devnull, "w") as quiet:
            stdout, sys.stdout = sys.stdout, quiet
            try:
                export_schema(project, snapshot({"product": PRODUCT}))
            finally:
                sys.stdout = stdout
        git(self.main, "add", "-A", "database")
        git(self.main, "commit", "-q", "-m", "baseline")
        git(self.main, "worktree", "add", "-q", "-b", "feature", str(self.worktree))
        self.state = base / "state.json"
        self.database({"product": PRODUCT})
        hook = Path(os.fsdecode(git(self.main, "rev-parse", "--path-format=absolute", "--git-path", "hooks/pre-commit")).strip())
        script = base / "hook.py"
        script.write_text(HOOK.format(docker=str(ROOT / "docker"), tests=str(ROOT / "tests"), state=str(self.state)))
        hook.write_text(f"#!/bin/sh\nexec {sys.executable} '{script}'\n")
        hook.chmod(0o755)

    def database(self, tables, action="commit_schema"):
        self.state.write_text(json.dumps({"root": str(self.main), "tables": tables, "action": action}))

    def commit(self, checkout, name="notes.txt"):
        (checkout / name).write_text(f"change in {checkout.name}\n")
        git(checkout, "add", name)
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        # Git sends a hook's output to stderr.
        return subprocess.run(["git", "-C", str(checkout), "commit", "-q", "-m", "change"], stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, env=env)

    def test_worktree_commit_checks_its_own_index_and_leaves_the_main_checkout_alone(self):
        main = tree(self.main)
        result = self.commit(self.worktree)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Schema matches the commit (1 tables)", result.stdout)
        self.assertEqual(tree(self.main), main)
        self.assertEqual(git(self.main, "status", "--porcelain"), b"")

    def test_worktree_schema_change_is_exported_and_drafted_in_the_worktree(self):
        main = tree(self.main)
        self.database({"product": PRODUCT, "feed": FEED})
        result = self.commit(self.worktree)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("schema not staged: table-feed.sql", result.stdout)
        self.assertNotIn("table-product.sql", result.stdout)
        self.assertIn(f"Exported 2 tables to {self.worktree / 'database/schema'}", result.stdout)
        self.assertIn(f"git add -A {self.worktree / 'database'}", result.stdout)
        self.assertTrue((self.worktree / "database/schema/table-feed.sql").is_file())
        drafts = list((self.worktree / "database/migrations").glob("*-schema.sql"))
        self.assertEqual(len(drafts), 1)
        self.assertIn(str(drafts[0]), result.stdout)
        self.assertEqual(tree(self.main), main, "the main checkout is neither exported into nor drafted in")
        git(self.worktree, "add", "-A", "database")
        result = self.commit(self.worktree, "second.txt")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Schema matches the commit (2 tables)", result.stdout)
        self.assertEqual(git(self.worktree, "show", "--name-only", "--format=", "HEAD").decode().split(),
                         sorted(["database/migrations/" + drafts[0].name, "database/schema/schema.manifest.json",
                                 "database/schema/table-feed.sql", "notes.txt", "second.txt"]))
        self.assertEqual(tree(self.main), main)

    def test_older_worktree_branch_is_stopped_against_the_live_db(self):
        # The main branch moved on; the worktree's branch still has the older snapshot.
        self.database({"product": PRODUCT, "feed": FEED})
        main_failure = self.commit(self.main)
        self.assertEqual(main_failure.returncode, 1, main_failure.stdout)
        git(self.main, "add", "-A", "database")
        self.assertEqual(self.commit(self.main, "second.txt").returncode, 0)
        main = tree(self.main)
        result = self.commit(self.worktree)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("schema not staged: table-feed.sql", result.stdout)
        self.assertTrue((self.worktree / "database/schema/table-feed.sql").is_file())
        self.assertEqual(tree(self.main), main)

    def test_main_checkout_commit_is_unchanged(self):
        result = self.commit(self.main)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Schema matches the commit (1 tables)", result.stdout)
        self.database({"product": PRODUCT, "feed": FEED})
        result = self.commit(self.main, "second.txt")
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"Exported 2 tables to {self.main / 'database/schema'}", result.stdout)
        self.assertIn(f"git add -A {self.main / 'database'}", result.stdout)
        self.assertFalse((self.worktree / "database/schema/table-feed.sql").exists())
        self.assertFalse((self.worktree / "database/migrations").exists())

    def test_schema_check_in_a_hook_manager_reads_the_worktree_index(self):
        self.database({"product": PRODUCT}, action="check_schema")
        git(self.worktree, "rm", "-q", "--cached", "database/schema/table-product.sql")
        result = self.commit(self.worktree)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("not staged: table-product.sql", result.stdout)
        git(self.worktree, "add", "database/schema/table-product.sql")
        result = self.commit(self.worktree)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Schema matches the Git index (1 tables)", result.stdout)

    def test_only_a_linked_worktree_of_the_same_repository_moves_the_schema_directory(self):
        project = type("Project", (), {"root": self.main, "settings": {"SCHEMA_DIRECTORY": "database/schema"}})()
        other = self.main.parent / "other"
        other.mkdir()
        git(other, "init", "-q")
        cwd = Path.cwd()
        self.addCleanup(os.chdir, cwd)
        for directory, expected in ((self.main, self.main), (other, self.main), (self.main.parent, self.main),
                                    (self.worktree / "database", self.worktree)):
            os.chdir(directory)
            with open(os.devnull, "w") as quiet:
                stdout, sys.stdout = sys.stdout, quiet
                try:
                    seen = in_committing_checkout(project)
                finally:
                    sys.stdout = stdout
            self.assertEqual(schema_directory(seen), expected.resolve() / "database/schema", directory)
            self.assertEqual(seen is project, expected == self.main, "the settings project itself is never changed")
        self.assertEqual(schema_directory(project), self.main.resolve() / "database/schema")


if __name__ == "__main__":
    unittest.main()
