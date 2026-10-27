from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.config import settings
from app.middleware import SecurityHeadersMiddleware
from app.routers import auth as auth_router
from app.routers import (
    config_editor,
    connections,
    connectivity,
    containers,
    dns_scanner,
    env_editor,
    health,
    scanner,
    system,
    system_proxy,
    traffic,
    usage,
)
from app.services import (
    connections_service,
    connectivity_service,
    docker_service,
    store,
    usage_service,
)
from app.services import (
    health as health_service,
)
from app.static_files import CachedStaticFiles


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init()
    connectivity_service.load_cache()
    # Opt-in; when off, erase any history from a prior opt-in.
    if settings.connection_tracking:
        usage_service.recorder.start()
    else:
        store.wipe_usage()
    health_service.monitor.start()
    health_service.recorder.start()
    # One /connections feed; usage and the live view only when tracking is opted in.
    connections_service.collector.start(track=settings.connection_tracking, taps=(health_service.recorder.feed,))
    yield
    await health_service.recorder.stop()
    await health_service.monitor.stop()
    await connections_service.collector.stop()
    if settings.connection_tracking:
        await usage_service.recorder.flush_and_stop()
    docker_service.close_client()


app = FastAPI(title="Proxy Dashboard", version="0.1.0", lifespan=lifespan)

app.add_middleware(SecurityHeadersMiddleware)

_routers = (
    auth_router,
    containers,
    connectivity,
    config_editor,
    system_proxy,
    system,
    env_editor,
    scanner,
    dns_scanner,
    traffic,
    connections,
    usage,
    health,
)
for module in _routers:
    app.include_router(module.router, prefix="/api/v1")

static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount("/", CachedStaticFiles(directory=str(static_dir), html=True), name="static")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.bind, port=settings.port)
