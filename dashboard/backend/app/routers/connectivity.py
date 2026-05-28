import asyncio
from collections.abc import AsyncGenerator

from fastapi import APIRouter

from app.models.schemas import ConnectivityResult
from app.services import connectivity_service, docker_service
from app.sse import event, sse_response

router = APIRouter(prefix="/connectivity", tags=["connectivity"])


@router.post("/test", response_model=list[ConnectivityResult])
async def test_all():
    containers = await asyncio.to_thread(docker_service.list_dashboard_containers)
    testable = docker_service.filter_testable(containers)
    return await asyncio.gather(*[connectivity_service.test_proxy_connectivity(c) for c in testable])


@router.get("/test/stream")
async def test_stream():
    """Stream per-proxy connectivity results as they complete.

    Emits one `services` event with the list of services about to be tested,
    one `result` event per service in completion order, and a final `done` event.
    """
    containers = await asyncio.to_thread(docker_service.list_dashboard_containers)
    testable = docker_service.filter_testable(containers)

    async def gen() -> AsyncGenerator[str, None]:
        yield event("services", {"services": [c.name for c in testable]})
        if not testable:
            yield event("done", {})
            return

        tasks = [asyncio.create_task(connectivity_service.test_proxy_connectivity(c)) for c in testable]
        try:
            for coro in asyncio.as_completed(tasks):
                result = await coro
                yield event("result", result.model_dump(mode="json"))
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
        yield event("done", {})

    return sse_response(gen())


@router.get("/test/{container_name}", response_model=ConnectivityResult)
async def test_single(container_name: str):
    container = await asyncio.to_thread(docker_service.find_dashboard_container, container_name)
    if not container:
        return ConnectivityResult(service=container_name, success=False, error="Container not found", tested_via="n/a")
    return await connectivity_service.test_proxy_connectivity(container)
