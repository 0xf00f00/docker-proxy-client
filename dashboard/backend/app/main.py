from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.routers import config_editor, connectivity, containers, env_editor, system, system_proxy
from app.services import docker_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    docker_service.close_client()


app = FastAPI(title="Proxy Dashboard", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(containers.router, prefix="/api/v1")
app.include_router(connectivity.router, prefix="/api/v1")
app.include_router(config_editor.router, prefix="/api/v1")
app.include_router(system_proxy.router, prefix="/api/v1")
app.include_router(system.router, prefix="/api/v1")
app.include_router(env_editor.router, prefix="/api/v1")

static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

if __name__ == "__main__":
    import uvicorn

    from app.config import settings

    uvicorn.run("app.main:app", host=settings.bind, port=settings.port)
