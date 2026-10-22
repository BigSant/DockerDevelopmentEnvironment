"""The project files create-project writes: templates/minimal and the shared Makefile."""

from pathlib import Path


TEMPLATES = Path(__file__).resolve().parent / "templates"
MAKEFILE = TEMPLATES / "shared/Makefile"
MINIMAL_TEMPLATE = TEMPLATES / "minimal"


def minimal_files(root):
    """Only the core sources used by create-project; extensions remain in setup."""
    root = Path(root).resolve()
    target = root / 'app'
    files = {target / path.relative_to(MINIMAL_TEMPLATE): path.read_bytes()
             for path in MINIMAL_TEMPLATE.rglob('*') if path.is_file()}
    files[target / 'Makefile'] = MAKEFILE.read_bytes()
    for path in files:
        files[path] = files[path].replace(b'__PROJECT_NAME__', root.name.encode())
        files[path] = files[path].replace(b'__PROJECT_DOMAIN__', (root.name.replace('_', '-') + '.local').encode())
    return target, files
