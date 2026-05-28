"""System-proxy controller plugin system.

A "system proxy" is the container that routes the host's traffic (clash,
xray, sing-box, …). At most one is active at a time, designated by the
`dashboard.controller=<name>` label on its container.

To add a new controller:

  1. Implement `SystemProxyController` in a new module under this package.
  2. Register it by calling `registry.register("<name>", FactoryFn)` at
     module import time (see `clash.py` for the canonical example).
  3. Add `dashboard.controller=<name>` to the container's labels in
     docker-compose.yml.

The HTTP router in `routers/system_proxy.py` is intentionally generic and
does not know about any specific controller.
"""

# Import side-effects: each controller module registers itself on import.
from app.services.system_proxy import clash  # noqa: F401
from app.services.system_proxy.base import SystemProxyController
from app.services.system_proxy.registry import (
    get_active_controller,
    register,
)

__all__ = [
    "SystemProxyController",
    "get_active_controller",
    "register",
]
