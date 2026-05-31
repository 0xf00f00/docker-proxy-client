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
from app.sse import event, sse_response

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/containers", tags=["containers"])

HEARTBEAT_SEC = 10.0
EVENT_DEBOUNCE_SEC = 0.3

# Docker actions that affect what the dashboard cares about. Health-status
# events come through as "health_status: healthy" etc., so we match on prefix.
STATE_ACTIONS = {"start", "stop", "die", "kill", "restart", "create", "destroy", "pause", "unpause"}


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
    try:
        await asyncio.to_thread(docker_service.restart_container, container_name)
        return ContainerActionResponse(success=True, message=f"Container {container_name} restarted")
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail="Container not found") from None
    except Exception:
        logger.exception("Failed to restart container %s", container_name)
        raise HTTPException(status_code=500, detail="Container action failed") from None


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
    try:
        await asyncio.to_thread(docker_service.stop_container, container_name)
        return ContainerActionResponse(success=True, message=f"Container {container_name} stopped")
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail="Container not found") from None
    except Exception:
        logger.exception("Failed to stop container %s", container_name)
        raise HTTPException(status_code=500, detail="Container action failed") from None


@router.get("/{container_name}/logs", dependencies=[RequireAuth])
async def get_logs(container_name: str, tail: int = 100):
    try:
        logs = await asyncio.to_thread(docker_service.get_container_logs, container_name, tail)
        return {"logs": logs}
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail="Container not found") from None
    except Exception:
        logger.exception("Failed to fetch logs for %s", container_name)
        raise HTTPException(status_code=500, detail="Failed to fetch logs") from None


@router.get("/stream")
async def stream_containers():
    """Push container state changes in near real-time.

    Subscribes to the Docker events stream so transitions (start/stop/restart/
    die/health_status/…) trigger an immediate snapshot, instead of waiting for
    the next poll tick. A ``HEARTBEAT_SEC`` periodic tick still runs so the
    stream stays healthy and recovers if the events socket drops.
    """
    loop = asyncio.get_running_loop()
    wake = asyncio.Event()
    stopped = threading.Event()

    try:
        client = docker_service.get_client()
    except Exception:
        logger.exception("Cannot connect to Docker for stream")
        raise HTTPException(status_code=503, detail="Cannot connect to Docker") from None

    event_stream = client.events(decode=True, filters={"type": "container"})

    def listen() -> None:
        try:
            for ev in event_stream:
                if stopped.is_set():
                    return
                action = str(ev.get("Action") or ev.get("status") or "")
                head = action.split(":", 1)[0].strip()
                if head in STATE_ACTIONS or head == "health_status":
                    try:
                        loop.call_soon_threadsafe(wake.set)
                    except RuntimeError:
                        return
        except Exception:
            # Stream closed or daemon disconnected — fall back to heartbeat polling.
            pass

    threading.Thread(target=listen, daemon=True).start()

    async def gen() -> AsyncGenerator[str]:
        last_payload: str | None = None
        try:
            while True:
                try:
                    containers = await asyncio.to_thread(docker_service.list_dashboard_containers)
                    payload = ContainerListResponse(
                        containers=containers, host_lan_ip=settings.host_lan_ip
                    ).model_dump_json()
                    if payload != last_payload:
                        yield f"event: snapshot\ndata: {payload}\n\n"
                        last_payload = payload
                    else:
                        yield ": ping\n\n"
                except Exception as e:
                    yield event("stream-error", {"detail": str(e)})

                try:
                    await asyncio.wait_for(wake.wait(), timeout=HEARTBEAT_SEC)
                except TimeoutError:
                    continue
                wake.clear()
                # Coalesce bursts (a `docker restart` fires kill+die+start within ms)
                await asyncio.sleep(EVENT_DEBOUNCE_SEC)
                wake.clear()
        finally:
            stopped.set()
            with contextlib.suppress(Exception):
                event_stream.close()

    return sse_response(gen())


@router.get("/{container_name}/logs/stream", dependencies=[RequireAuth])
async def stream_container_logs(container_name: str, tail: int = 200):
    """Follow a container's logs over SSE.

    Emits ``chunk`` events carrying the raw decoded log stream (carriage
    returns, ANSI escapes and all) for an xterm.js terminal to render, plus a
    terminal ``end`` event when the container exits or the client disconnects.
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

    log_stream = container.logs(stream=True, follow=True, timestamps=True, tail=tail)

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
