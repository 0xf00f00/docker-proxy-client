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
    scanner,
    system,
    system_proxy,
    traffic,
    usage,
)
from app.services import connections_service, connectivity_service, docker_service, store, usage_service
from app.static_files import CachedStaticFiles


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init()
    connectivity_service.load_cache()
    # Opt-in; when off, erase any history from a prior opt-in.
    if settings.connection_tracking:
        # One always-on /connections feed drives both live snapshots and usage;
        # the recorder just runs its flush loop.
        usage_service.recorder.start()
        connections_service.collector.start()
    else:
        store.wipe_usage()
    yield
    if settings.connection_tracking:
        await connections_service.collector.stop()
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
)
for module in _routers:
    app.include_router(module.router, prefix="/api/v1")

static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount("/", CachedStaticFiles(directory=str(static_dir), html=True), name="static")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.bind, port=settings.port)
