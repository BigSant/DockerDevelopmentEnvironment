"""In-memory stand-in for the Database client (docker/database_client.DatabaseClient) in A-level tests.

It never parses SQL: a test answers reads with a function (state it needs lives in that function's closure)
and checks what was executed, loaded and dumped. Assign it as project.database."""

import contextlib
import io
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "docker"))
from database_client import DatabaseError


class FakeDatabase:
    def __init__(self, answer=None, fail_on=None, dump_output=None):
        # answer(sql) -> rows (tuples of str) for query, bytes for extract; a list answers reads in order.
        if isinstance(answer, list):
            replies = iter(answer)
            answer = lambda sql: next(replies)
        self.answer = answer or (lambda sql: [])
        # fail_on(operation, text) -> True raises DatabaseError; operation is read/write/load/dump.
        self.fail_on = fail_on or (lambda operation, text: False)
        # dump_output(options, schema) -> bytes the dump stream yields.
        self.dump_output = dump_output or (lambda options, schema: b"")
        self.queried, self.extracted, self.executed, self.loaded, self.dumps = [], [], [], [], []

    def _check(self, operation, text):
        if self.fail_on(operation, text):
            raise DatabaseError(f"{operation} failed (fake)")

    def query(self, sql, timeout=60):
        self.queried.append(sql)
        self._check("read", sql)
        return [tuple(row) for row in self.answer(sql)]

    def extract(self, sql, timeout=900):
        self.extracted.append(sql)
        self._check("read", sql)
        return self.answer(sql) or b""

    def execute(self, sql, timeout=None):
        self.executed.append(sql)
        self._check("write", sql)

    @contextlib.contextmanager
    def load(self):
        sink = io.BytesIO()
        try:
            yield sink
        finally:
            self.loaded.append(sink.getvalue())
        self._check("load", self.loaded[-1].decode("utf-8", "replace"))

    @contextlib.contextmanager
    def dump(self, *options, schema=None):
        self.dumps.append((list(options), schema))
        yield io.BytesIO(self.dump_output(list(options), schema))
        self._check("dump", " ".join(options))
