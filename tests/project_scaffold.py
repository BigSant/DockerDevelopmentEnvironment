"""Test projects as create-project writes them: templates/minimal and the shared Makefile in <root>/app."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from prepare_project import minimal_files


def scaffold(root, files=None):
    """Write create-project's files and the extra {relative path: text} files; return the app directory."""
    app, sources = minimal_files(root)
    for path, content in sources.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    for relative, text in (files or {}).items():
        path = app / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return app
