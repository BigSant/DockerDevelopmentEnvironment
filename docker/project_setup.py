"""make setup: run the one-action commands in order, stop at the first failure, never touch the DB."""
import subprocess

from database_client import DatabaseError


def database_tables(project):
    """Number of tables in the project database, or None when it cannot be asked."""
    try:
        rows = project.database.query('SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE();\n')
    except DatabaseError:
        return None
    return int(rows[0][0]) if rows and rows[0][0].isdigit() else None


def git_hooks(project):
    """A clean clone gets the git hooks too (schema check, the project's bin/pre-commit and bin/pre-push);
    an existing hook manager or a hook somebody else wrote is left alone."""
    if project.environment != 'local':
        return
    from git_hooks import install_git_hooks
    try:
        install_git_hooks(project)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f'Git hooks not installed: {error}')


def run_setup(project, timeout=90):
    from project_application import prepare_application
    from project_bootstrap import bootstrap
    from project_environment import initialize_directories
    from project_health import start_project
    from project_images import build_images
    steps = [
        ('init', lambda p: (initialize_directories(p), p)[1]),
        # bootstrap writes the missing domain, ports and database values and returns the project read again
        # with them; the later steps must start the services from those, not from the stale settings.
        ('bootstrap', lambda p: (lambda fresh: (git_hooks(fresh), fresh)[1])(bootstrap(p))),
        ('build', lambda p: (build_images(p), p)[1]),
        ('app-prepare', lambda p: (prepare_application(p), p)[1]),
        ('up', lambda p: (start_project(p, timeout), p)[1]),
    ]
    for number, (name, step) in enumerate(steps, 1):
        print(f'==> [{number}/{len(steps)}] {name}', flush=True)
        project = step(project)
    tables = database_tables(project)
    database = project.settings['DATABASE_NAME']
    if tables:
        print(f'Setup complete. Database {database}: {tables} tables (left as it was).')
    else:
        print(f'Setup complete. Database {database} is empty — load data deliberately:\n'
              f'  make db-load set=dev          # versioned dev data set\n'
              f'  make db-import file=dump.sql  # a LIVE dump')
    return project
