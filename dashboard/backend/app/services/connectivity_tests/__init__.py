"""Per-protocol connectivity test registry.

Each protocol (socks5, http, tls, …) has a dedicated test function. The
`connectivity_service` looks up the function via this registry. To add a
new protocol, drop a module that calls `register(...)` at import time.
"""

# Import side-effects: each module registers its protocols on import.
from app.services.connectivity_tests import http_socks, tls  # noqa: F401
from app.services.connectivity_tests.registry import get_test, register

__all__ = ["get_test", "register"]
