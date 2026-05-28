import re
import subprocess
from pathlib import Path

from app.config import settings

_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")


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


def _service_profiles(service_name: str) -> list[str]:
    """Extract the profiles a service belongs to from docker-compose.yml."""
    from ruamel.yaml import YAML

    compose_file = _project_path() / "docker-compose.yml"
    if not compose_file.is_file():
        return []
    try:
        yaml = YAML(typ="safe")
        data = yaml.load(compose_file.read_text())
        return data.get("services", {}).get(service_name, {}).get("profiles", []) or []
    except Exception:
        return []


def _run_compose(service_name: str, action_args: list[str]) -> tuple[bool, str]:
    if not _safe_name(service_name):
        return False, "Invalid service name"
    project = _project_path()
    project_name = _detect_project_name(service_name)
    profiles = _service_profiles(service_name)

    cmd = ["docker", "compose"]
    if project_name and _safe_name(project_name):
        cmd.extend(["--project-name", project_name])
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
            timeout=60,
        )
    except FileNotFoundError:
        return False, "docker CLI not available in this environment"
    except subprocess.TimeoutExpired:
        return False, "docker compose timed out after 60s"

    if result.returncode != 0:
        return False, result.stderr.strip() or result.stdout.strip() or "Unknown error"
    return True, result.stdout.strip()


def recreate_service(service_name: str) -> tuple[bool, str]:
    """Run `docker compose up -d --force-recreate <service>` to apply env changes."""
    return _run_compose(service_name, ["up", "-d", "--force-recreate"])


def restart_service(service_name: str) -> tuple[bool, str]:
    """Run `docker compose restart <service>` to bounce the process so it re-reads its config file."""
    return _run_compose(service_name, ["restart"])
