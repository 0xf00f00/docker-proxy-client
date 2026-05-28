import fcntl
import socket
import struct

from pydantic_settings import BaseSettings


def _get_interface_ip(interface: str) -> str | None:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        return socket.inet_ntoa(
            fcntl.ioctl(sock.fileno(), 0x8915, struct.pack("256s", interface[:15].encode("utf-8")))[20:24]
        )
    except OSError:
        return None


def _detect_host_lan_ip(interface: str) -> str:
    ip = _get_interface_ip(interface)
    if ip:
        return ip
    # Fallback: connect to a public IP to find the default route source address
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("1.1.1.1", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except OSError:
        return "127.0.0.1"


class Settings(BaseSettings):
    host_lan_interface: str = "eth0"
    host_lan_ip: str = ""
    clash_api_url: str = "http://127.0.0.1:9090"
    clash_api_secret: str = ""
    docker_host: str = "unix:///var/run/docker.sock"
    configs_base_path: str = "/configs"
    compose_project_path: str = "/compose"
    port: int = 8080
    bind: str = "0.0.0.0"

    model_config = {"env_prefix": "DASHBOARD_", "env_file": ".env"}

    def model_post_init(self, __context: object) -> None:
        if not self.host_lan_ip:
            self.host_lan_ip = _detect_host_lan_ip(self.host_lan_interface)


settings = Settings()
