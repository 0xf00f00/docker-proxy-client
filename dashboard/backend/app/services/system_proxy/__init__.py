"""System-proxy controller plugin system.

A "system proxy" is the container that routes the host's traffic (clash,
xray, sing-box, …). At most one is active at a time, designated by the
`dashboard.controller=<name>` label on its container.

To add a new controller:

  1. Implement `SystemProxyController` in a new module under this package.
  2. Optionally implement capability protocols (`SupportsConnectionsStream`,
     `SupportsTrafficStream`) — translating the backend's wire format into the
     normalized `ConnectionSnapshot`. Features auto-enable for controllers that
     implement them and stay hidden for those that don't (see `active_capabilities`).
  3. Register it by calling `registry.register("<name>", FactoryFn)` at
     module import time (see `clash.py` for the canonical example).
  4. Add `dashboard.controller=<name>` to the container's labels in
     docker-compose.yml.

The HTTP router in `routers/system_proxy.py` is intentionally generic and
does not know about any specific controller.
"""

# Import side-effects: each controller module registers itself on import.
from app.services.system_proxy import clash  # noqa: F401
from app.services.system_proxy.base import Capability, SystemProxyController
from app.services.system_proxy.registry import (
    active_capabilities,
    get_active_container,
    get_active_controller,
    register,
)

__all__ = [
    "Capability",
    "SystemProxyController",
    "active_capabilities",
    "get_active_container",
    "get_active_controller",
    "register",
]
