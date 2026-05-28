import asyncio
import random
from collections.abc import AsyncGenerator

from fastapi import APIRouter

from app.models.schemas import ConnectivityResult, ContainerInfo
from app.services import connectivity_service, docker_service
from app.sse import event, sse_response

router = APIRouter(prefix="/connectivity", tags=["connectivity"])

# Bounding concurrent in-flight probes and randomizing launch spacing prevents
# saturating limited uplinks — otherwise blasting every proxy at once can cause
# spurious timeouts and false negatives.
MAX_CONCURRENT_PROBES = 2
LAUNCH_DELAY_MIN_S = 0.15
LAUNCH_DELAY_MAX_S = 0.5


async def _run_staggered(testable: list[ContainerInfo]) -> AsyncGenerator[ConnectivityResult, None]:
    sem = asyncio.Semaphore(MAX_CONCURRENT_PROBES)

    async def probe(c: ContainerInfo, delay: float) -> ConnectivityResult:
        await asyncio.sleep(delay)
        async with sem:
            return await connectivity_service.test_proxy_connectivity(c)

    delays: list[float] = []
    acc = 0.0
    for _ in testable:
        delays.append(acc)
        acc += random.uniform(LAUNCH_DELAY_MIN_S, LAUNCH_DELAY_MAX_S)

    tasks = [asyncio.create_task(probe(c, d)) for c, d in zip(testable, delays)]
    try:
        for coro in asyncio.as_completed(tasks):
            yield await coro
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()


@router.post("/test", response_model=list[ConnectivityResult])
async def test_all():
    containers = await asyncio.to_thread(docker_service.list_dashboard_containers)
    testable = docker_service.filter_testable(containers)
    results: list[ConnectivityResult] = []
    async for r in _run_staggered(testable):
        results.append(r)
    return results


@router.get("/test/stream")
async def test_stream():
    """Stream per-proxy connectivity results as they complete.

    Emits one `services` event with the list of services about to be tested,
    one `result` event per service in completion order, and a final `done` event.
    Probes are launched with jittered delays and a concurrency cap so limited
    networks don't suffer cross-test interference.
    """
    containers = await asyncio.to_thread(docker_service.list_dashboard_containers)
    testable = docker_service.filter_testable(containers)

    async def gen() -> AsyncGenerator[str, None]:
        yield event("services", {"services": [c.name for c in testable]})
        if not testable:
            yield event("done", {})
            return

        async for result in _run_staggered(testable):
            yield event("result", result.model_dump(mode="json"))
        yield event("done", {})

    return sse_response(gen())


@router.get("/test/{container_name}", response_model=ConnectivityResult)
async def test_single(container_name: str):
    container = await asyncio.to_thread(docker_service.find_dashboard_container, container_name)
    if not container:
        return ConnectivityResult(service=container_name, success=False, error="Container not found", tested_via="n/a")
    return await connectivity_service.test_proxy_connectivity(container)
