import fcntl
import os
import socket
import struct

from pydantic_settings import BaseSettings


def _interface_ip(interface: str) -> str | None:
    """IPv4 currently bound to `interface`, or None if it has none / is absent."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        return socket.inet_ntoa(
            fcntl.ioctl(sock.fileno(), 0x8915, struct.pack("256s", interface[:15].encode("utf-8")))[20:24]
        )
    except OSError:
        return None


class Settings(BaseSettings):
    host_lan_interface: str = "eth0"
    clash_api_url: str = "http://127.0.0.1:9697"
    clash_api_secret: str = ""
    docker_host: str = "unix:///var/run/docker.sock"
    configs_base_path: str = "/configs"
    compose_project_path: str = "/compose"
    port: int = 8080
    bind: str = "0.0.0.0"
    password: str = ""
    state_dir: str = "/state"
    connection_tracking: bool = False
    health_monitoring: bool = True

    model_config = {"env_prefix": "DASHBOARD_", "env_file": ".env"}

    @property
    def host_lan_ip(self) -> str:
        override = os.environ.get("DASHBOARD_HOST_LAN_IP", "").strip()
        if override:
            return override
        return _interface_ip(self.host_lan_interface) or "127.0.0.1"


settings = Settings()
