from collections.abc import Awaitable, Callable

from app.models.schemas import ConnectivityResult, ContainerInfo

TestFn = Callable[[ContainerInfo], Awaitable[ConnectivityResult]]

_REGISTRY: dict[str, TestFn] = {}


def register(*protocols: str) -> Callable[[TestFn], TestFn]:
    """Decorator that registers a coroutine as the test for one or more protocols."""

    def decorate(fn: TestFn) -> TestFn:
        for p in protocols:
            _REGISTRY[p] = fn
        return fn

    return decorate


def get_test(protocol: str) -> TestFn | None:
    """Look up a registered test. The protocol may be a compound like
    `socks5+tls` — the first segment (before `+`) is the lookup key."""
    return _REGISTRY.get(protocol.split("+")[0])
