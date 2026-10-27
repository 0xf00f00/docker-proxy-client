import os
from pathlib import Path

import docker
from docker.errors import NotFound
from docker.models.containers import Container

from app.config import settings
from app.models.schemas import ContainerInfo, DashboardLabels

_client: docker.DockerClient | None = None

SOCKET_PATHS = [
    os.environ.get("DOCKER_HOST", ""),
    "unix:///var/run/docker.sock",
    f"unix://{Path.home()}/.docker/run/docker.sock",
    "unix:///run/docker.sock",
]

CATEGORY_ORDER = {"proxy": 0, "dns": 1, "infra": 2}


def _connect(timeout: int) -> docker.DockerClient:
    for path in SOCKET_PATHS:
        if not path:
            continue
        try:
            client = docker.DockerClient(base_url=path, timeout=timeout)
            client.ping()
            return client
        except Exception:
            continue

    raise RuntimeError("Cannot connect to Docker. Tried: " + ", ".join(p for p in SOCKET_PATHS if p))


def get_client() -> docker.DockerClient:
    global _client
    if _client is None:
        _client = _connect(timeout=60)
    return _client


def new_client(timeout: int) -> docker.DockerClient:
    return _connect(timeout)


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
    """`host_ip:port` for a host-published binding of `port` that other LAN devices can reach.

    All-interfaces bindings map to the host's LAN IP; loopback bindings are skipped, since a phone
    on the LAN can't use them even though the dashboard's own backend could.
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


def _network_ip(container: Container, *, macvlan: bool) -> str | None:
    networks = container.attrs.get("NetworkSettings", {}).get("Networks") or {}
    for net_name, net_config in networks.items():
        if ("direct_internet" in net_name) == macvlan and net_config.get("IPAddress"):
            return net_config["IPAddress"]
    return None


def _resolve_address(container: Container, labels: DashboardLabels, macvlan_order: tuple[bool, ...]) -> str | None:
    """`labels.port` is the port a user dials; the host side comes from Docker's live config:
    host network, then a LAN-reachable published port, then the first network IP in `macvlan_order`."""
    if labels.port is None:
        return None
    if _is_host_network(container):
        return f"{settings.host_lan_ip}:{labels.port}"
    published = _find_published_lan_address(container, labels.port)
    if published:
        return published
    for macvlan in macvlan_order:
        ip = _network_ip(container, macvlan=macvlan)
        if ip:
            return f"{ip}:{labels.port}"
    return None


def resolve_lan_address(container: Container, labels: DashboardLabels) -> str | None:
    """What a LAN device types into its proxy app. Loopback-only or internal services return None."""
    return _resolve_address(container, labels, (True,))


def resolve_probe_address(container: Container, labels: DashboardLabels) -> str | None:
    """Where the dashboard tests the container: the bridge IP is preferred because Linux blocks host
    traffic to macvlan IPs on the host's own parent interface."""
    return _resolve_address(container, labels, (False, True))


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
        probe_address=resolve_probe_address(container, labels),
    )


def _compose_labels_to_dict(labels: object) -> dict[str, str]:
    """Normalize compose `labels` (list `["k=v"]` or map `{k: v}`) to a dict."""
    if isinstance(labels, dict):
        return {str(k): str(v) for k, v in labels.items()}
    if isinstance(labels, list):
        out: dict[str, str] = {}
        for item in labels:
            key, _, value = str(item).partition("=")
            out[key.strip()] = value.strip()
        return out
    return {}


def _declared_container_info(service_name: str, service: dict) -> ContainerInfo | None:
    """Build a stopped "off" ContainerInfo from a compose service declaration.

    For an on-demand proxy with no live container yet; network-derived fields stay
    null since they can't be resolved without a running container.
    """
    labels = parse_dashboard_labels(_compose_labels_to_dict(service.get("labels")))
    if labels is None:
        return None
    name = str(service.get("container_name") or service_name)
    if not labels.name:
        labels.name = name
    return ContainerInfo(
        id=name,
        name=name,
        image=str(service.get("image") or ""),
        status="stopped",
        health=None,
        started_at=None,
        dashboard=labels,
        lan_address=None,
        probe_address=None,
    )


def _find_declared_service(name: str) -> ContainerInfo | None:
    """Resolve a dashboard service from docker-compose.yml by service key or container_name."""
    from app.services import env_service

    try:
        services = env_service.load_compose_services()
    except Exception:
        return None

    service = services.get(name)
    resolved_name = name
    if service is None:
        for service_name, candidate in services.items():
            if str(candidate.get("container_name") or service_name) == name:
                service, resolved_name = candidate, service_name
                break
    if service is None:
        return None
    return _declared_container_info(resolved_name, service)


def declared_ondemand_containers(live_names: set[str]) -> list[ContainerInfo]:
    """Off cards for profiled services not in `live_names`.

    Services behind a compose `profiles:` aren't created by a plain `up`, so they
    have no live container to list; surface them from their declaration instead.
    """
    from app.services import env_service

    result: list[ContainerInfo] = []
    try:
        services = env_service.load_compose_services()
    except Exception:
        return result

    for service_name, service in services.items():
        if not (service.get("profiles") or []):
            continue
        name = str(service.get("container_name") or service_name)
        if name in live_names:
            continue
        info = _declared_container_info(service_name, service)
        if info is not None:
            result.append(info)
    return result


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
    result.extend(declared_ondemand_containers({c.name for c in result}))
    result.sort(key=lambda c: (CATEGORY_ORDER.get(c.dashboard.category, 3), c.dashboard.name))
    return result


def find_dashboard_container(name: str) -> ContainerInfo | None:
    """Look up one dashboard-enabled container by name. Cheaper than `list_dashboard_containers`.

    Falls back to the compose declaration when no container exists yet, so callers
    (e.g. the config editor) can resolve an on-demand service before it's started.
    """
    try:
        container = get_client().containers.get(name)
    except NotFound:
        return _find_declared_service(name)
    labels = parse_dashboard_labels(container.labels)
    if labels is None:
        return None
    return _build_container_info(container, labels)


def filter_testable(containers: list[ContainerInfo]) -> list[ContainerInfo]:
    """Containers that should be probed for connectivity.

    Skips containers that have a healthcheck declared but aren't `healthy` —
    probing a `starting` or `unhealthy` proxy wastes a request slot on a
    near-guaranteed failure that would also pollute the result UI.
    """
    return [
        c
        for c in containers
        if c.dashboard.testable
        and c.probe_address
        and c.status == "running"
        and (c.health is None or c.health == "healthy")
    ]


def restart_container(container_name: str) -> None:
    container = get_client().containers.get(container_name)
    container.restart(timeout=10)


def start_container(container_name: str) -> None:
    container = get_client().containers.get(container_name)
    container.start()


def stop_container(container_name: str) -> None:
    container = get_client().containers.get(container_name)
    container.stop(timeout=10)


def get_container_logs(container_name: str, tail: int = 100) -> str:
    container = get_client().containers.get(container_name)
    return container.logs(tail=tail, timestamps=True).decode("utf-8", errors="replace")


def get_container_logs_before(container_name: str, until: float, limit: int = 500) -> str:
    """Last `limit` log lines that occurred before the `until` epoch timestamp.

    Powers scroll-up history paging: the viewer passes the oldest timestamp it
    holds and gets back the chunk preceding it (timestamps included so the
    client can keep paging and de-dupe the boundary line).
    """
    container = get_client().containers.get(container_name)
    return container.logs(tail=limit, until=until, timestamps=True).decode("utf-8", errors="replace")
