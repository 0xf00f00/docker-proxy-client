"""System-proxy controller plugins. The active one is the container labelled `dashboard.controller=<name>`.

To add one: implement `SystemProxyController` in a module here (plus any capability protocols; features
auto-enable per `active_capabilities`), call `registry.register("<name>", Factory)` at import (see
`clash.py`), and add the label in docker-compose.yml. `routers/system_proxy.py` stays controller-agnostic.
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
