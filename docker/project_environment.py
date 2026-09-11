"""Prepare local files and check prerequisites without starting project services."""

import json
import os
from pathlib import Path
import re
import socket
import subprocess


def initialize_env(directory, environment):
    directory = Path(directory).resolve()
    grouped = (directory / "env/common.env").is_file()
    private = directory / (f"env/{environment}.env" if grouped else f".env.{environment}")
    if private.exists():
        return
    example = private.with_name(private.name + ".example")
    if not example.is_file():
        raise ValueError(f"Missing private environment template: {example}")
    with private.open("xb") as handle:
        private.chmod(0o600)
        handle.write(example.read_bytes())
    print(f"Created {private}; set real credentials, domain and unused ports before starting.")


def initialize_directories(project):
    created = []
    permitted = (project.root / "data", project.root / "app/config", project.directory / "config", project.directory / ".generated/test/data")
    for service in project.model()["services"].values():
        for mount in service.get("volumes", []):
            if mount["type"] != "bind":
                continue
            path = Path(mount["source"])
            # File mounts and the application checkout must be supplied by the user.
            if path.exists() or path.suffix or not any(path.is_relative_to(base) for base in permitted):
                continue
            if not path.resolve().is_relative_to(project.root):
                raise ValueError(f"Refusing a mount directory outside the project: {path}")
            path.mkdir(parents=True, exist_ok=True)
            created.append(path)
    print(f"Prepared {len(created)} runtime directories; existing files and application code preserved.")


def docker(*arguments):
    try:
        return subprocess.run(["docker", *arguments], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("Docker is unavailable or did not respond within 20 seconds") from error


def owned_ports(project):
    listed = docker("ps", "-q", "--filter", f"label=com.docker.compose.project={project.name}")
    if listed.returncode:
        raise ValueError("Cannot inspect running project containers")
    ids = listed.stdout.split()
    if not ids:
        return set()
    result = docker("inspect", *ids)
    if result.returncode:
        raise ValueError("Cannot inspect running project ports")
    return {(int(binding["HostPort"]), container_port.rsplit("/", 1)[1])
            for container in json.loads(result.stdout)
            for container_port, bindings in container["NetworkSettings"]["Ports"].items()
            for binding in bindings or []}


def pull_images(project):
    # cron reuses the PHP image without its own build definition. Do not try
    # pulling that project-local tag from a registry, even when PHP is inactive.
    built_images = {service.get("image") for service in project.model()["services"].values()
                    if service.get("build")}
    active = json.loads(project.capture(["config", "--format", "json"]))["services"]
    external = [name for name, service in active.items()
                if service.get("image") and service["image"] not in built_images]
    if external:
        project.run(["pull", "--ignore-buildable", *external])
    else:
        print("No external images in the selected services; use make build for local images.")


def doctor(project):
    from project_storage import require_space
    from project_policy import validate_configuration
    require_space(project, project.data_directory)
    validate_configuration(project)
    engine = docker("info", "--format", "{{.ServerVersion}}")
    if engine.returncode:
        raise ValueError("Docker Engine is not available; start Docker first")
    version = docker("compose", "version", "--short")
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", version.stdout)
    if version.returncode or not match or tuple(map(int, match.groups())) < (2, 24, 4):
        raise ValueError("Docker Compose 2.24.4 or newer is required")
    model = json.loads(project.capture(["config", "--format", "json"]))
    issues = set()
    private = project.env_files[-1]
    if private.stat().st_mode & 0o077:
        issues.add(f"Private env permissions are too broad; chmod 600 {private}")
    if "replace-with-" in private.read_text():
        issues.add(f"Fill in actual settings in {private}")
    claimed = owned_ports(project)
    for name, service in model["services"].items():
        image = service.get("image")
        if image and docker("image", "inspect", image).returncode:
            action = "make build" if service.get("build") else "make pull"
            issues.add(f"Missing image for {name}: {image}; use {action} with the selected PROFILES")
        for mount in service.get("volumes", []):
            if mount["type"] != "bind":
                continue
            path = Path(mount["source"])
            if not path.exists():
                issues.add(f"Missing bind source for {name}: {path}; run make init or supply the file/checkout")
            elif not mount.get("read_only") and not os.access(path, os.W_OK):
                issues.add(f"Bind source is not writable by your user: {path}")
        for port in service.get("ports", []):
            published = str(port.get("published", ""))
            if not published:
                continue
            if not published.isdigit() or not 1 <= int(published) <= 65535:
                issues.add(f"Choose an explicit valid host port for {name}")
                continue
            protocol = port.get("protocol", "tcp")
            if (int(published), protocol) in claimed:
                continue
            host = port.get("host_ip") or "0.0.0.0"
            family = socket.AF_INET6 if ":" in host else socket.AF_INET
            kind = socket.SOCK_DGRAM if protocol == "udp" else socket.SOCK_STREAM
            with socket.socket(family, kind) as probe:
                try:
                    probe.bind((host, int(published)))
                except OSError:
                    issues.add(f"Host port {host}:{published}/{protocol} is unavailable for {name}")
    # Check the actual configured certificate directory, including prod overrides.
    proxy = model["services"].get("nginx-proxy", {})
    for mount in proxy.get("volumes", []):
        if mount["target"] == "/etc/ssl/certs":
            for filename in ("domain.crt", "domain.key"):
                certificate = Path(mount["source"]) / filename
                if not certificate.is_file() or not os.access(certificate, os.R_OK):
                    issues.add(f"Missing or unreadable TLS file: {certificate}; provision host certificates")
    if issues:
        raise ValueError("Environment is not ready:\n  " + "\n  ".join(sorted(issues)))
    print(f"Ready: {project.name}; Docker {engine.stdout.strip()}, Compose {version.stdout.strip()}.")
    print("Active service sources, local images, host ports, mount directories and TLS files checked.")
    if getattr(project, 'settings', {}).get('PROFILE') in ('ps', 'prestashop'):
        running = project.capture(['ps', '--status', 'running', '--services']).split()
        if 'php-fpm' in running:
            from project_health import smoke
            smoke(project)
        else:
            print('PrestaShop runtime checks pending: start the environment with make up.')
