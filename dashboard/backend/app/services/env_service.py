import re
import subprocess
from pathlib import Path

from app.config import settings

_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")

_BUILD_TIMEOUT_SEC = 300


def _safe_name(value: str) -> bool:
    return bool(_NAME_RE.match(value))


def _project_path() -> Path:
    candidates = [
        Path(settings.compose_project_path),
        Path.cwd(),
        Path.cwd().parent.parent,  # backend/ -> dashboard/ -> repo root
    ]
    for p in candidates:
        if (p / ".env").is_file() or (p / "docker-compose.yml").is_file():
            return p
    raise FileNotFoundError("Compose project root not found")


def _env_path() -> Path:
    return _project_path() / ".env"


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key, _, value = stripped.partition("=")
    key = key.strip()
    value = value.strip()
    # Strip surrounding quotes if present
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return key, value


def read_env(keys: list[str]) -> dict[str, str]:
    """Read specific keys from .env. Missing keys return as empty strings."""
    env_path = _env_path()
    found: dict[str, str] = {}
    if env_path.exists():
        for raw_line in env_path.read_text().splitlines():
            parsed = _parse_env_line(raw_line)
            if parsed and parsed[0] in keys:
                found[parsed[0]] = parsed[1]
    return {k: found.get(k, "") for k in keys}


def write_env(updates: dict[str, str]) -> None:
    """Update specific keys in .env in-place, preserving comments and ordering."""
    for key in updates:
        if not _KEY_RE.match(key):
            raise ValueError(f"Invalid env var name: {key}")

    env_path = _env_path()
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    seen: set[str] = set()
    new_lines: list[str] = []

    for line in lines:
        parsed = _parse_env_line(line)
        if parsed and parsed[0] in updates:
            key = parsed[0]
            new_lines.append(f"{key}={_quote_if_needed(updates[key])}")
            seen.add(key)
        else:
            new_lines.append(line)

    for key, value in updates.items():
        if key not in seen:
            new_lines.append(f"{key}={_quote_if_needed(value)}")

    tmp = env_path.with_suffix(".env.tmp")
    tmp.write_text("\n".join(new_lines) + "\n")
    tmp.replace(env_path)


def _quote_if_needed(value: str) -> str:
    if value == "" or any(c in value for c in (" ", "\t", "#", "$", "'", '"')):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _detect_project_name(service_name: str) -> str | None:
    """Read the original compose project name from a running container's labels."""
    try:
        from app.services.docker_service import get_client

        container = get_client().containers.get(service_name)
        return (container.labels or {}).get("com.docker.compose.project")
    except Exception:
        return None


def _detect_any_project_name() -> str | None:
    """Project name borrowed from any compose container.

    An on-demand service has no container of its own to read it from yet, and
    compose's default (the in-container cwd basename) would be wrong.
    """
    try:
        from app.services.docker_service import get_client

        for container in get_client().containers.list(all=True):
            project = (container.labels or {}).get("com.docker.compose.project")
            if project:
                return project
    except Exception:
        return None
    return None


def _host_project_dir() -> str | None:
    """Host path of the project, from the Source of the container's compose mount.

    `docker compose` runs inside the dashboard container, but the daemon resolves
    bind mounts against the host filesystem, so it needs the host path. The mount
    Source is authoritative; the `project.working_dir` label can't be trusted —
    containers the dashboard created from `/compose` carry a poisoned value.
    """
    target = settings.compose_project_path
    try:
        from app.services.docker_service import get_client

        for container in get_client().containers.list(all=True):
            for mount in container.attrs.get("Mounts", []) or []:
                if mount.get("Destination") == target and mount.get("Source"):
                    return mount["Source"]
    except Exception:
        return None
    return None


def _compose_file_args(project: Path) -> list[str]:
    """`-f` flags for the local compose files, base before override.

    Explicit because `--project-directory` moves compose's file discovery to the
    host dir, which isn't readable from inside this container.
    """
    args: list[str] = []
    for filename in (
        "docker-compose.yml",
        "docker-compose.yaml",
        "docker-compose.override.yml",
        "docker-compose.override.yaml",
    ):
        path = project / filename
        if path.is_file():
            args.extend(["-f", str(path)])
    return args


def load_compose_services() -> dict:
    """Parse the ``services`` map from docker-compose.yml (empty dict on error)."""
    from ruamel.yaml import YAML

    compose_file = _project_path() / "docker-compose.yml"
    if not compose_file.is_file():
        return {}
    try:
        yaml = YAML(typ="safe")
        data = yaml.load(compose_file.read_text())
        return data.get("services", {}) or {}
    except Exception:
        return {}


def _service_profiles(service_name: str) -> list[str]:
    """Extract the profiles a service belongs to from docker-compose.yml."""
    return load_compose_services().get(service_name, {}).get("profiles", []) or []


def _run_compose(service_name: str, action_args: list[str], timeout: int = 60) -> tuple[bool, str]:
    if not _safe_name(service_name):
        return False, "Invalid service name"
    project = _project_path()
    project_name = _detect_project_name(service_name) or _detect_any_project_name()
    profiles = _service_profiles(service_name)
    host_dir = _host_project_dir()

    cmd = ["docker", "compose"]
    if project_name and _safe_name(project_name):
        cmd.extend(["--project-name", project_name])
    # Resolve bind mounts against the host path, but read the compose files and
    # .env from the container FS via explicit -f/--env-file (see _host_project_dir).
    if host_dir and host_dir != str(project):
        cmd.extend(["--project-directory", host_dir])
        cmd.extend(_compose_file_args(project))
        env_file = project / ".env"
        if env_file.is_file():
            cmd.extend(["--env-file", str(env_file)])
    for profile in profiles:
        if _safe_name(profile):
            cmd.extend(["--profile", profile])
    cmd.extend(action_args)
    cmd.append("--")
    cmd.append(service_name)

    try:
        result = subprocess.run(
            cmd,
            cwd=str(project),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return False, "docker CLI not available in this environment"
    except subprocess.TimeoutExpired:
        return False, f"docker compose timed out after {timeout}s"

    if result.returncode != 0:
        return False, result.stderr.strip() or result.stdout.strip() or "Unknown error"
    return True, result.stdout.strip()


def recreate_service(service_name: str) -> tuple[bool, str]:
    """Run `docker compose up -d --force-recreate <service>` to apply env changes."""
    return _run_compose(service_name, ["up", "-d", "--force-recreate"], timeout=_BUILD_TIMEOUT_SEC)


def restart_service(service_name: str) -> tuple[bool, str]:
    """Run `docker compose restart <service>` to bounce the process so it re-reads its config file."""
    return _run_compose(service_name, ["restart"])


def start_service(service_name: str) -> tuple[bool, str]:
    """Run `docker compose up -d <service>` to create-and-start an on-demand service."""
    return _run_compose(service_name, ["up", "-d"], timeout=_BUILD_TIMEOUT_SEC)
