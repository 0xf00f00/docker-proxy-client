"""Client for a Go scanner's control API (cf-edge-manager, dns-scanner)."""

import json
import urllib.error
import urllib.request
from collections.abc import Iterator

from app.services import docker_service

TIMEOUT_S = 2.0
# Above the scanners' 15s SSE heartbeat, so a healthy idle stream isn't torn down.
EVENT_READ_TIMEOUT_S = 30.0


class ScannerApi:
    def __init__(self, base_url: str, container: str, *, secret: str = "", events_path: str = "/events") -> None:
        self.base_url = base_url
        self.container = container
        self._headers = {"X-Scanner-Secret": secret} if secret else {}
        self._events_path = events_path

    def running(self) -> bool:
        try:
            return docker_service.get_client().containers.get(self.container).status == "running"
        except Exception:
            return False

    def get(self, path: str) -> dict | None:
        req = urllib.request.Request(f"{self.base_url}{path}", headers=self._headers)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                return json.load(resp)
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            return None

    def post(self, path: str, body: dict | None = None) -> dict | None:
        headers = dict(self._headers)
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(body).encode() if body is not None else b"",
            method="POST",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            raw = resp.read()
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            return None

    def events(self) -> Iterator[None]:
        """Yield once per pushed event; a change signal only. Raises or returns on disconnect."""
        req = urllib.request.Request(
            f"{self.base_url}{self._events_path}", headers={**self._headers, "Accept": "text/event-stream"}
        )
        with urllib.request.urlopen(req, timeout=EVENT_READ_TIMEOUT_S) as resp:
            for raw in resp:
                if raw.startswith(b"data:"):
                    yield
