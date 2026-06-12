import asyncio
import contextlib
import random
from collections.abc import AsyncGenerator

from fastapi import APIRouter

from app.models.schemas import ConnectivityResult, ConnectivityResultsResponse, ContainerInfo
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

# How long a cached result counts as "fresh" for auto-probe decisions. Long by
# design: results carry their age in the UI and a manual "Test All" is always one
# tap away, so we don't re-probe a limited uplink on every refresh or device
# switch — only when the data is genuinely old or missing.
DEFAULT_FRESH_MAX_AGE_S = 6 * 60 * 60  # 6 hours


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


@router.get("/results", response_model=ConnectivityResultsResponse)
async def cached_results(max_age: float = DEFAULT_FRESH_MAX_AGE_S):
    """Return last-known *fresh* results from the shared cache without probing."""
    containers = await asyncio.to_thread(docker_service.list_dashboard_containers)
    testable = docker_service.filter_testable(containers)
    stale = any(not connectivity_service.is_fresh(c.name, max_age) for c in testable)
    fresh = [r for r in connectivity_service.cached_results() if connectivity_service.is_fresh(r.service, max_age)]
    return ConnectivityResultsResponse(results=fresh, stale=stale)


@router.get("/test/stream")
async def test_stream(max_age: float = DEFAULT_FRESH_MAX_AGE_S):
    """Stream per-proxy connectivity results as they complete.

    With `max_age > 0` (the default) only proxies whose cached result is missing
    or older than `max_age` are re-probed; fresh ones are replayed from the cache
    instantly. Pass `max_age=0` to force a full re-test (the manual "Test All").

    Emits one `services` event listing the services about to be *probed* (so fresh
    cards never flash a spinner), one `result` event per service — replayed-fresh
    first, then freshly-probed in completion order — and a final `done` event.
    Probes are launched with jittered delays and a concurrency cap so limited
    networks don't suffer cross-test interference.
    """
    containers = await asyncio.to_thread(docker_service.list_dashboard_containers)
    testable = docker_service.filter_testable(containers)
    fresh_names = {c.name for c in testable if max_age > 0 and connectivity_service.is_fresh(c.name, max_age)}
    stale = [c for c in testable if c.name not in fresh_names]

    async def gen() -> AsyncGenerator[str]:
        yield event("services", {"services": [c.name for c in stale]})
        # Replay cached results for the fresh ones so a fresh page hydrates fully.
        for c in testable:
            if c.name in fresh_names:
                cached = connectivity_service.cached_result(c.name)
                if cached is not None:
                    yield event("result", cached.model_dump(mode="json"))
        if not stale:
            yield event("done", {})
            return

        async for result in _run_staggered(stale):
            yield event("result", result.model_dump(mode="json"))
        yield event("done", {})

    return sse_response(gen())


@router.get("/test/{container_name}", response_model=ConnectivityResult)
async def test_single(container_name: str):
    container = await asyncio.to_thread(docker_service.find_dashboard_container, container_name)
    if not container:
        return ConnectivityResult(service=container_name, success=False, error="Container not found", tested_via="n/a")
    return await connectivity_service.test_proxy_connectivity(container)


def _stream_probe(container_name: str, run):
    """SSE wrapper shared by the stability probes. `run(container, on_event)` is
    the probe coroutine; its events are forwarded live and its return value is
    sent as a final `result`. The probe runs as a background task feeding a
    queue, so a client disconnect cancels it (tearing down in-flight httpx
    requests); a heartbeat every 10s keeps intermediaries from dropping an idle
    stream.
    """

    async def gen() -> AsyncGenerator[str]:
        container = await asyncio.to_thread(docker_service.find_dashboard_container, container_name)
        if not container:
            yield event("error", {"detail": "Container not found"})
            yield event("done", {})
            return
        if not container.dashboard.testable or not container.probe_address:
            yield event("error", {"detail": "Not testable or no probe address"})
            yield event("done", {})
            return

        queue: asyncio.Queue[tuple[str, dict] | None] = asyncio.Queue()

        async def on_event(name: str, data: dict) -> None:
            await queue.put((name, data))

        async def runner() -> None:
            try:
                result = await run(container, on_event)
                await queue.put(("result", result.model_dump(mode="json")))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await queue.put(("error", {"detail": str(exc)}))
            finally:
                await queue.put(None)

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


@router.get("/stability/{container_name}/stream")
async def test_stability_stream(container_name: str):
    """Stability probe, streamed (see docs/realtime-stability-repro.md).

    DISRUPTIVE: it briefly saturates the tunnel to reproduce download drops and
    call degradation, so it hurts any live user for its duration. On-demand only.
    """
    return _stream_probe(container_name, stability.test_stability)
