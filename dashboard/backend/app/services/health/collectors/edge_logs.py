"""Edge handshake attempts and failures, counted by following sni-spoofing's log."""

import contextlib
import logging
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterable

logger = logging.getLogger(__name__)

KINDS = ("hello", "ack_to", "dial_to", "unreach", "dial_err", "rst")
RETRY_S = 2.0
MAX_RETRY_S = 60.0


def classify(line: str) -> str | None:
    if "fake ClientHello sent" in line:
        return "hello"
    if "ACK timeout" in line:
        return "ack_to"
    if "Failed to connect" in line:
        if "i/o timeout" in line:
            return "dial_to"
        if "unreachable" in line or "no route to host" in line:
            return "unreach"
        return "dial_err"
    if "injector aborted handshake" in line:
        return "rst"
    return None


class EdgeLogFollower:
    def __init__(self, names: Iterable[str], client_factory: Callable[[], object]) -> None:
        self._names = tuple(names)
        self._client_factory = client_factory
        self._lock = threading.Lock()
        self._counts: dict[str, Counter[str]] = {n: Counter() for n in self._names}
        self._following: set[str] = set()
        self._streams: dict[str, object] = {}
        self._stop = threading.Event()

    def start(self) -> None:
        self._stop.clear()
        for name in self._names:
            threading.Thread(target=self._follow, args=(name,), daemon=True, name=f"health-edge-{name}").start()

    def stop(self) -> None:
        self._stop.set()
        for stream in list(self._streams.values()):
            with contextlib.suppress(Exception):
                stream.close()

    def feed_lines(self, name: str, lines: Iterable[str]) -> None:
        hits = [k for k in map(classify, lines) if k]
        if hits:
            with self._lock:
                self._counts[name].update(hits)

    def drain(self) -> tuple[dict[str, dict[str, int]], list[str]]:
        """Counts per followed container, and the containers not being followed."""
        with self._lock:
            out = {n: {k: self._counts[n][k] for k in KINDS} for n in self._names if n in self._following}
            blind = [n for n in self._names if n not in self._following]
            for c in self._counts.values():
                c.clear()
        return out, blind

    def _set_following(self, name: str, on: bool) -> None:
        with self._lock:
            (self._following.add if on else self._following.discard)(name)

    def _follow(self, name: str) -> None:
        delay = RETRY_S
        while not self._stop.is_set():
            client = None
            try:
                client = self._client_factory()
                stream = client.containers.get(name).logs(stream=True, follow=True, since=int(time.time()))
                self._streams[name] = stream
                self._set_following(name, True)
                delay = RETRY_S
                buf = b""
                for chunk in stream:
                    *lines, buf = (buf + chunk).split(b"\n")
                    self.feed_lines(name, (ln.decode("utf-8", "replace") for ln in lines))
            except Exception:
                logger.debug("edge log follow for %s ended", name, exc_info=True)
                delay = min(delay * 2, MAX_RETRY_S)
            finally:
                self._set_following(name, False)
                self._streams.pop(name, None)
                if client is not None:
                    with contextlib.suppress(Exception):
                        client.close()
            self._stop.wait(delay)
