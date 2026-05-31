from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.middleware import SecurityHeadersMiddleware
from app.routers import auth as auth_router
from app.routers import config_editor, connectivity, containers, env_editor, scanner, system, system_proxy, traffic
from app.services import docker_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    docker_service.close_client()


app = FastAPI(title="Proxy Dashboard", version="0.1.0", lifespan=lifespan)

app.add_middleware(SecurityHeadersMiddleware)

_routers = (auth_router, containers, connectivity, config_editor, system_proxy, system, env_editor, scanner, traffic)
for module in _routers:
    app.include_router(module.router, prefix="/api/v1")

static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

if __name__ == "__main__":
    import uvicorn

    from app.config import settings

    uvicorn.run("app.main:app", host=settings.bind, port=settings.port)
