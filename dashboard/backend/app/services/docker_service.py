import os
from pathlib import Path

import docker
from docker.errors import NotFound
from docker.models.containers import Container

from app.config import settings
from app.models.schemas import ContainerInfo, DashboardLabels

_client: docker.DockerClient | None = None

SOCKET_PATHS = [
    settings.docker_host,
    os.environ.get("DOCKER_HOST", ""),
    "unix:///var/run/docker.sock",
    f"unix://{Path.home()}/.docker/run/docker.sock",
    "unix:///run/docker.sock",
]

CATEGORY_ORDER = {"proxy": 0, "dns": 1, "infra": 2}


def get_client() -> docker.DockerClient:
    global _client
    if _client is not None:
        return _client

    for path in SOCKET_PATHS:
        if not path:
            continue
        try:
            _client = docker.DockerClient(base_url=path)
            _client.ping()
            return _client
        except Exception:
            _client = None

    raise RuntimeError("Cannot connect to Docker. Tried: " + ", ".join(p for p in SOCKET_PATHS if p))


def close_client() -> None:
    """Close the cached Docker client, if any."""
    global _client
    if _client is not None:
        try:
            _client.close()
        finally:
            _client = None


def parse_dashboard_labels(labels: dict[str, str]) -> DashboardLabels | None:
    prefix = "dashboard."
    raw = {k[len(prefix) :].replace(".", "_"): v for k, v in labels.items() if k.startswith(prefix)}
    if raw.get("enable", "").lower() != "true":
        return None
    if "env" in raw:
        raw["env"] = [k.strip() for k in raw["env"].split(",") if k.strip()]
    return DashboardLabels.model_validate(raw)


def _is_host_network(container: Container) -> bool:
    return container.attrs.get("HostConfig", {}).get("NetworkMode") == "host"


def _find_published_lan_address(container: Container, port: int) -> str | None:
    """
    Find a host-published binding whose host-side port == `port` AND is
    reachable from other devices on the LAN. Returns `"host_ip:port"` or None.

    - All-interfaces bindings (`0.0.0.0`, `::`, empty) → host's detected LAN IP.
    - Loopback bindings (`127.x`, `::1`) → skipped. Reachable from the host
      itself but not from a phone or laptop on the LAN, so they don't count
      as a LAN address even though the dashboard's own backend could hit them.
    - Specific non-loopback IPs → reported verbatim.
    """
    ports = container.attrs.get("NetworkSettings", {}).get("Ports") or {}
    for bindings in ports.values():
        for b in bindings or []:
            host_port = b.get("HostPort")
            if not host_port or int(host_port) != port:
                continue
            host_ip = b.get("HostIp", "")
            if host_ip in ("", "0.0.0.0", "::"):
                return f"{settings.host_lan_ip}:{host_port}"
            if host_ip == "::1" or host_ip.startswith("127."):
                continue
            return f"{host_ip}:{host_port}"
    return None


def _get_macvlan_ip(container: Container) -> str | None:
    """Get IP from the direct_internet (macvlan) network."""
    networks = container.attrs.get("NetworkSettings", {}).get("Networks") or {}
    for net_name, net_config in networks.items():
        if "direct_internet" in net_name:
            ip = net_config.get("IPAddress")
            if ip:
                return ip
    return None


def resolve_lan_address(container: Container, labels: DashboardLabels) -> str | None:
    """
    Resolve the address users type into their proxy app on the LAN: `<host>:<port>`.

    "LAN" is the contract: this is what any device on the local network can
    connect to. Services only reachable on loopback don't qualify and return
    None — surfacing them would invite tests that pass for the dashboard but
    fail for the user's phone.

    `labels.port` is the single source of truth for the port — the port a
    user dials. The host side is always derived from Docker's live config:

      1. `network_mode: host` → host's LAN IP
      2. Port published on a LAN-reachable interface → that interface IP
      3. Attached to the `direct_internet` macvlan → the container's macvlan IP
      4. Otherwise → None (internal-only or loopback-only)
    """
    if labels.port is None:
        return None

    if _is_host_network(container):
        return f"{settings.host_lan_ip}:{labels.port}"

    published = _find_published_lan_address(container, labels.port)
    if published:
        return published

    macvlan_ip = _get_macvlan_ip(container)
    if macvlan_ip:
        return f"{macvlan_ip}:{labels.port}"

    return None


def get_container_health(container: Container) -> str | None:
    health = container.attrs.get("State", {}).get("Health")
    return health.get("Status") if health else None


def _build_container_info(container: Container, labels: DashboardLabels) -> ContainerInfo:
    if not labels.name:
        labels.name = container.name or container.short_id
    return ContainerInfo(
        id=container.short_id,
        name=container.name or container.short_id,
        image=str(container.image.tags[0] if container.image.tags else container.image.short_id),
        status=container.status,
        health=get_container_health(container),
        started_at=container.attrs.get("State", {}).get("StartedAt"),
        dashboard=labels,
        lan_address=resolve_lan_address(container, labels),
    )


def list_dashboard_containers() -> list[ContainerInfo]:
    global _client
    try:
        client = get_client()
        containers = client.containers.list(all=True)
    except Exception:
        _client = None
        raise

    result = [
        _build_container_info(c, labels) for c in containers if (labels := parse_dashboard_labels(c.labels)) is not None
    ]
    result.sort(key=lambda c: (CATEGORY_ORDER.get(c.dashboard.category, 3), c.dashboard.name))
    return result


def find_dashboard_container(name: str) -> ContainerInfo | None:
    """Look up one dashboard-enabled container by name. Cheaper than `list_dashboard_containers`."""
    try:
        container = get_client().containers.get(name)
    except NotFound:
        return None
    labels = parse_dashboard_labels(container.labels)
    if labels is None:
        return None
    return _build_container_info(container, labels)


def filter_testable(containers: list[ContainerInfo]) -> list[ContainerInfo]:
    """Containers that should be probed for connectivity."""
    return [c for c in containers if c.dashboard.testable and c.lan_address and c.status == "running"]


def restart_container(container_name: str) -> None:
    container = get_client().containers.get(container_name)
    container.restart(timeout=10)


def get_container_logs(container_name: str, tail: int = 100) -> str:
    container = get_client().containers.get(container_name)
    return container.logs(tail=tail, timestamps=True).decode("utf-8", errors="replace")
