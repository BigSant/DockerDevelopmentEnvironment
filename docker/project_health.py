"""Start containers and wait for their Docker healthchecks."""


def start_project(project, timeout=90):
    from project_policy import preflight_php
    preflight_php(project)
    # A directory a newer setup binds (data/metrics) did not exist when the project was initialised.
    from project_environment import runtime_directories
    created, replaced = runtime_directories(project)
    if created:
        print("Prepared runtime directories: " + ", ".join(str(path.relative_to(project.root)) for path in created))
    project.run(['up', '-d', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', str(timeout)])
    # A running container still holds the root-owned directory that was replaced.
    active = set(project.active_model()['services']) if replaced else set()
    if replaced & active:
        project.run(['up', '-d', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', str(timeout), '--force-recreate',
                     '--no-deps', *sorted(replaced & active)])
    from project_prestashop import ensure_htaccess
    ensure_htaccess(project)
