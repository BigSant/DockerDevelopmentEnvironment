"""make setup: run the one-action commands in order, stop at the first failure, never touch the DB."""
import subprocess

from database_client import READ


def database_tables(project):
    """Number of tables in the project database, or None when it cannot be asked."""
    command = project.command + ['exec', '-T', 'database', 'sh', '-c', READ, 'setup-status',
                                 project.settings['DATABASE_NAME']]
    result = subprocess.run(command, env=project.child_env(), input='SELECT COUNT(*) FROM information_schema.tables '
                            'WHERE table_schema = DATABASE();\n', capture_output=True, text=True)
    return int(result.stdout.strip()) if result.returncode == 0 and result.stdout.strip().isdigit() else None


def run_setup(project, timeout=90):
    from project_application import prepare_application
    from project_bootstrap import bootstrap
    from project_environment import initialize_directories
    from project_health import start_project
    steps = [
        ('init', lambda p: (initialize_directories(p), p)[1]),
        ('bootstrap', bootstrap),
        ('build', lambda p: (p.run(['build', 'php-fpm-base'], profiles='build_only'),
                             p.run(['build', 'php-fpm-core'], profiles='build_only'), p.run(['build']), p)[-1]),
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
