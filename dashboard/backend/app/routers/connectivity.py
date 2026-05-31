import asyncio
import contextlib
import random
from collections.abc import AsyncGenerator

from fastapi import APIRouter

from app.models.schemas import ConnectivityResult, ContainerInfo, StabilityResult
from app.services import connectivity_service, docker_service
from app.services.connectivity_tests import stability
from app.sse import comment, event, sse_response

router = APIRouter(prefix="/connectivity", tags=["connectivity"])

# Bounding concurrent in-flight probes and randomizing launch spacing prevents
# saturating limited uplinks — otherwise blasting every proxy at once can cause
# spurious timeouts and false negatives.
MAX_CONCURRENT_PROBES = 2
LAUNCH_DELAY_MIN_S = 0.15
LAUNCH_DELAY_MAX_S = 0.5


async def _run_staggered(testable: list[ContainerInfo]) -> AsyncGenerator[ConnectivityResult]:
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

    tasks = [asyncio.create_task(probe(c, d)) for c, d in zip(testable, delays, strict=True)]
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

    async def gen() -> AsyncGenerator[str]:
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


async def _stability_error(container_name: str, detail: str) -> StabilityResult:
    regime = await stability.regime_mod.get_regime()
    return StabilityResult(
        service=container_name, grade="inconclusive", tested_via="n/a", regime=regime,
        attempts=0, ok=0, resets=0, timeouts=0, other_errors=0,
        failure_rate=0.0, failure_rate_lower=0.0, error=detail,
    )


@router.get("/stability/{container_name}/stream")
async def test_stability_stream(container_name: str):
    """Deep stability probe, streamed (see docs/proxy-stability-detection.md).

    Emits live `phase`/`regime`/`progress` events, then a final `result` (or
    `error`) and `done`. The probe runs as a background task feeding a queue, so
    a client disconnect cancels it (tearing down in-flight httpx requests); a
    heartbeat every 10s keeps intermediaries from dropping an idle stream.
    """
    container = await asyncio.to_thread(docker_service.find_dashboard_container, container_name)

    async def gen() -> AsyncGenerator[str]:
        if not container:
            result = await _stability_error(container_name, "Container not found")
            yield event("result", result.model_dump(mode="json"))
            yield event("done", {})
            return
        if not container.dashboard.testable or not container.probe_address:
            result = await _stability_error(container_name, "Not testable or no probe address")
            yield event("result", result.model_dump(mode="json"))
            yield event("done", {})
            return

        queue: asyncio.Queue[tuple[str, dict] | None] = asyncio.Queue()

        async def on_event(name: str, data: dict) -> None:
            await queue.put((name, data))

        async def runner() -> None:
            try:
                result = await stability.test_stability(container, on_event=on_event)
                await queue.put(("result", result.model_dump(mode="json")))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await queue.put(("error", {"detail": str(exc)}))
            finally:
                await queue.put(None)  # sentinel: runner finished

        task = asyncio.create_task(runner())
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=10.0)
                except TimeoutError:
                    yield comment()  # heartbeat — proves the stream is alive
                    continue
                if item is None:
                    break
                name, data = item
                yield event(name, data)
            yield event("done", {})
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    return sse_response(gen())
