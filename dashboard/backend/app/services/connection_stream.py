"""Shared driver for the active controller's live-connections feed."""

import asyncio
import logging
from collections.abc import Callable

from app.services.system_proxy.base import ConnectionSnapshot, SupportsConnectionsStream

logger = logging.getLogger(__name__)

# No connections-capable controller yet — re-check at a slow idle cadence.
NO_SOURCE_RETRY_SEC = 5.0
# Backoff after a stream fault before reconnecting.
ERROR_RETRY_SEC = 3.0


async def drive_connections(
    consume: Callable[[ConnectionSnapshot], None],
    *,
    on_no_source: Callable[[], None] | None = None,
    on_error: Callable[[], None] | None = None,
) -> None:
    """Pump the active controller's live-connections feed until cancelled."""
    # Imported lazily to avoid a module-load cycle (registry -> docker_service).
    from app.services.system_proxy import registry

    while True:
        try:
            controller = await asyncio.to_thread(registry.get_active_controller)
            if controller is None or not isinstance(controller, SupportsConnectionsStream):
                if on_no_source is not None:
                    on_no_source()
                await asyncio.sleep(NO_SOURCE_RETRY_SEC)
                continue
            async for snapshot in controller.stream_connections():
                consume(snapshot)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("connections stream error; reconnecting", exc_info=True)
            if on_error is not None:
                on_error()
            await asyncio.sleep(ERROR_RETRY_SEC)
