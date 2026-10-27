import asyncio
import codecs
import contextlib
import logging
import threading
from collections.abc import AsyncGenerator

import docker.errors
from fastapi import APIRouter, HTTPException

from app.auth import RequireAuth
from app.config import settings
from app.models.schemas import ContainerActionResponse, ContainerListResponse
from app.services import docker_service, env_service
from app.sse import event, snapshot_stream, sse_response

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/containers", tags=["containers"])

HEARTBEAT_SEC = 10.0

# Docker actions that affect what the dashboard cares about. Health-status
# events come through as "health_status: healthy" etc., so we match on prefix.
STATE_ACTIONS = {"start", "stop", "die", "kill", "restart", "create", "destroy", "pause", "unpause"}


async def _docker(fn, *args, fail: str = "Container action failed"):
    """Run a blocking docker call, mapping NotFound to 404 and anything else to 500."""
    try:
        return await asyncio.to_thread(fn, *args)
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail="Container not found") from None
    except Exception:
        logger.exception("%s (%s)", fail, args)
        raise HTTPException(status_code=500, detail=fail) from None


@router.get("/", response_model=ContainerListResponse)
async def list_containers():
    try:
        containers = await asyncio.to_thread(docker_service.list_dashboard_containers)
    except Exception:
        logger.exception("Failed to list containers")
        raise HTTPException(status_code=503, detail="Cannot connect to Docker") from None
    return ContainerListResponse(containers=containers, host_lan_ip=settings.host_lan_ip)


@router.post("/{container_name}/restart", response_model=ContainerActionResponse, dependencies=[RequireAuth])
async def restart_container(container_name: str):
    await _docker(docker_service.restart_container, container_name)
    return ContainerActionResponse(success=True, message=f"Container {container_name} restarted")


@router.post("/{container_name}/start", response_model=ContainerActionResponse, dependencies=[RequireAuth])
async def start_container(container_name: str):
    try:
        await asyncio.to_thread(docker_service.start_container, container_name)
        return ContainerActionResponse(success=True, message=f"Container {container_name} started")
    except docker.errors.APIError as sdk_error:
        # Raw SDK start can't create a never-created profiled service (NotFound) or
        # reconcile a wedged one (500). Let compose drive it, then force-recreate.
        ok, message = await asyncio.to_thread(env_service.start_service, container_name)
        if not ok:
            logger.warning("compose up for %s failed (%s); retrying with --force-recreate", container_name, message)
            ok, message = await asyncio.to_thread(env_service.recreate_service, container_name)
        if not ok:
            logger.error("Failed to start %s via compose: %s (sdk: %s)", container_name, message, sdk_error)
            raise HTTPException(status_code=500, detail="Container action failed") from None
        return ContainerActionResponse(success=True, message=f"Container {container_name} started")
    except Exception:
        logger.exception("Failed to start container %s", container_name)
        raise HTTPException(status_code=500, detail="Container action failed") from None


@router.post("/{container_name}/stop", response_model=ContainerActionResponse, dependencies=[RequireAuth])
async def stop_container(container_name: str):
    await _docker(docker_service.stop_container, container_name)
    return ContainerActionResponse(success=True, message=f"Container {container_name} stopped")


@router.get("/{container_name}/logs", dependencies=[RequireAuth])
async def get_logs(container_name: str, tail: int = 100):
    return {"logs": await _docker(docker_service.get_container_logs, container_name, tail, fail="Failed to fetch logs")}


@router.get("/{container_name}/logs/history", dependencies=[RequireAuth])
async def get_logs_history(container_name: str, until: float, limit: int = 500):
    """Up to `limit` log lines older than the `until` epoch, for scroll-up paging."""
    limit = max(1, min(limit, 2000))
    text = await _docker(
        docker_service.get_container_logs_before, container_name, until, limit, fail="Failed to fetch logs"
    )
    return {"text": text}


@router.get("/stream")
async def stream_containers():
    """Push a container snapshot on every Docker state or health transition, plus a heartbeat floor."""
    try:
        client = docker_service.get_client()
    except Exception:
        logger.exception("Cannot connect to Docker for stream")
        raise HTTPException(status_code=503, detail="Cannot connect to Docker") from None

    def feed():
        for ev in client.events(decode=True, filters={"type": "container"}):
            head = str(ev.get("Action") or ev.get("status") or "").split(":", 1)[0].strip()
            if head in STATE_ACTIONS or head == "health_status":
                yield

    async def read() -> str:
        containers = await asyncio.to_thread(docker_service.list_dashboard_containers)
        return ContainerListResponse(containers=containers, host_lan_ip=settings.host_lan_ip).model_dump_json()

    return snapshot_stream("snapshot", read, interval=HEARTBEAT_SEC, feed=feed)


@router.get("/{container_name}/logs/stream", dependencies=[RequireAuth])
async def stream_container_logs(container_name: str, tail: int = 200, since: float = 0.0):
    """Follow a container's raw log stream (ANSI included, for xterm.js) as ``chunk`` events, then ``end``.

    ``since`` (epoch) backfills from that point to resume without a gap; otherwise start at the last ``tail`` lines.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue(maxsize=4000)

    try:
        client = docker_service.get_client()
        container = await asyncio.to_thread(client.containers.get, container_name)
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail="Container not found") from None
    except Exception:
        logger.exception("Cannot fetch container %s for log stream", container_name)
        raise HTTPException(status_code=503, detail="Cannot connect to Docker") from None

    log_kwargs: dict = {"stream": True, "follow": True, "timestamps": True}
    if since > 0:
        log_kwargs["since"] = since  # backfill the gap, then follow live
    else:
        log_kwargs["tail"] = tail
    log_stream = container.logs(**log_kwargs)

    def push(event_name: str, data: str) -> None:
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(queue.put_nowait, (event_name, data))

    def reader() -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            for chunk in log_stream:
                if not chunk:
                    continue
                text = decoder.decode(chunk)
                if text:
                    push("chunk", text)
            tail = decoder.decode(b"", final=True)
            if tail:
                push("chunk", tail)
        except Exception as e:
            push("stream-error", str(e))
        finally:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=reader, daemon=True).start()

    async def gen() -> AsyncGenerator[str]:
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                event_name, data = item
                if event_name == "chunk":
                    yield event("chunk", {"text": data})
                else:
                    yield event(event_name, {"detail": data})
            yield event("end", {})
        finally:
            with contextlib.suppress(Exception):
                log_stream.close()

    return sse_response(gen())
