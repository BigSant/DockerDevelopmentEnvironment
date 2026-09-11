"""Start containers and wait for their Docker healthchecks."""


def start_project(project, timeout=90):
    from project_policy import preflight_php
    preflight_php(project)
    project.run(['up', '-d', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', str(timeout)])
