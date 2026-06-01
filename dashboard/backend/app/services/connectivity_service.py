import asyncio
import random
from datetime import UTC, datetime

from app.models.schemas import ConnectivityResult, ContainerInfo
from app.services import connectivity_tests

# Retry params chosen so a single bad attempt on a flaky/limited network can't
# falsely fail a proxy: success is declared if any attempt succeeds, and the
# reported latency is the mean of the successful attempts.
RETRY_ATTEMPTS = 3
RETRY_DELAY_MIN_S = 0.2
RETRY_DELAY_MAX_S = 0.6

# Shared in-memory cache of the most recent result per service. The dashboard
# auto-probes on load, and a single uplink is shared by every viewer — so a
# refresh, a back-and-forth, or a second device should reuse a recent result
# instead of re-hammering a limited link. In-memory by design: a backend restart
# simply forces fresh probes on the next load.
_cache: dict[str, ConnectivityResult] = {}


def _finalize(result: ConnectivityResult) -> ConnectivityResult:
    """Stamp a result with the current time and write it through to the shared
    cache. Every connectivity probe returns via here, so the cache always holds
    the latest result per service."""
    result.tested_at = datetime.now(UTC)
    _cache[result.service] = result
    return result


def cached_result(name: str) -> ConnectivityResult | None:
    return _cache.get(name)


def cached_results() -> list[ConnectivityResult]:
    return list(_cache.values())


def is_fresh(name: str, max_age_s: float) -> bool:
    """True if a cached result for `name` exists and is younger than max_age_s."""
    result = _cache.get(name)
    if result is None or result.tested_at is None:
        return False
    age = (datetime.now(UTC) - result.tested_at).total_seconds()
    return age <= max_age_s


async def test_proxy_connectivity(container: ContainerInfo) -> ConnectivityResult:
    if not container.dashboard.testable or not container.probe_address:
        return _finalize(
            ConnectivityResult(
                service=container.name,
                success=False,
                error="Not testable or no probe address",
                tested_via="n/a",
            )
        )

    test_fn = connectivity_tests.get_test(container.dashboard.protocol)
    if test_fn is None:
        return _finalize(
            ConnectivityResult(
                service=container.name,
                success=False,
                error=f"Unsupported protocol: {container.dashboard.protocol}",
                tested_via="n/a",
            )
        )

    attempts: list[ConnectivityResult] = []
    for i in range(RETRY_ATTEMPTS):
        if i > 0:
            await asyncio.sleep(random.uniform(RETRY_DELAY_MIN_S, RETRY_DELAY_MAX_S))
        attempts.append(await test_fn(container))

    successes = [a for a in attempts if a.success]
    if successes:
        latencies = [a.latency_ms for a in successes if a.latency_ms is not None]
        avg_latency = round(sum(latencies) / len(latencies), 1) if latencies else None
        status_codes = [a.status_code for a in successes if a.status_code is not None]
        ip_info = next((a.ip_info for a in successes if a.ip_info is not None), None)
        return _finalize(
            ConnectivityResult(
                service=container.name,
                success=True,
                latency_ms=avg_latency,
                status_code=status_codes[0] if status_codes else None,
                tested_via=successes[-1].tested_via,
                ip_info=ip_info,
            )
        )

    last = attempts[-1]
    return _finalize(
        ConnectivityResult(
            service=container.name,
            success=False,
            latency_ms=last.latency_ms,
            error=last.error,
            tested_via=last.tested_via,
        )
    )
