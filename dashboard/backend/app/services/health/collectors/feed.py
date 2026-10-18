"""Per-minute aggregates of the shared live-connections feed. Hosts are never kept."""

import time
from collections import deque
from dataclasses import dataclass

NORES_MAX_LIFE_S = 30.0
EARLY_MAX_LIFE_S = 10.0
EARLY_MAX_DOWN = 1024
BULK_MIN_BYTES = 256 * 1024
CALL_MIN_BYTES = 120_000
NON_CALL_UDP_PORTS = frozenset({"53", "123", "443", "5353"})
MEET_UDP_PORTS = frozenset({"3478", *(str(p) for p in range(19302, 19310))})
PEAK_SPAN_S = 3.0
PEAK_WINDOW_S = 5.0


@dataclass(slots=True)
class _Flow:
    first: float
    user: bool
    chain: str
    tcp: bool
    dport: str
    up: int
    down: int
    pre: bool  # open before we started watching, so its lifetime is unknown
    m_up: int = 0
    m_down: int = 0
    m_active: int = 0


def _pct(sorted_vals: list[float], p: float) -> float:
    k = (len(sorted_vals) - 1) * p / 100
    f = int(k)
    if f + 1 >= len(sorted_vals):
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[f + 1] - sorted_vals[f]) * (k - f)


class FeedAggregator:
    def __init__(self) -> None:
        self._flows: dict[str, _Flow] = {}
        self._primed = False
        self._totals: tuple[int, int] | None = None
        self._window: deque[tuple[float, int, int]] = deque()
        self._new_minute()

    def _new_minute(self) -> None:
        self._frames = 0
        self._up = self._down = 0
        self._pk_up = self._pk_down = 0.0
        self._user = dict.fromkeys(("up", "down", "conns", "new", "closed", "tcp_closed", "nores", "early1k"), 0)
        self._chains: dict[str, dict[str, int]] = {}
        self._gone: list[_Flow] = []

    def _chain(self, name: str) -> dict[str, int]:
        ch = self._chains.get(name)
        if ch is None:
            ch = self._chains[name] = {"up": 0, "down": 0, "new": 0, "nores": 0}
        return ch

    def reset(self) -> None:
        self._flows.clear()
        self._primed = False
        self._totals = None
        self._window.clear()

    def _ingest_totals(self, up: int, down: int, now: float) -> None:
        if self._totals is not None:
            pu, pd = self._totals
            if up < pu or down < pd:
                self._window.clear()
            else:
                self._up += up - pu
                self._down += down - pd
        self._totals = (up, down)
        self._window.append((now, up, down))
        while now - self._window[0][0] > PEAK_WINDOW_S:
            self._window.popleft()
        base = None
        for entry in self._window:
            if now - entry[0] < PEAK_SPAN_S:
                break
            base = entry
        if base is not None:
            span = now - base[0]
            self._pk_up = max(self._pk_up, (up - base[1]) / span)
            self._pk_down = max(self._pk_down, (down - base[2]) / span)

    def _count(self, f: _Flow, du: int, dd: int) -> None:
        if not (du or dd):
            return
        f.m_up += du
        f.m_down += dd
        if dd:
            f.m_active += 1
        self._user["up"] += du
        self._user["down"] += dd
        ch = self._chain(f.chain)
        ch["up"] += du
        ch["down"] += dd

    def ingest(self, snap, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self._frames += 1
        self._ingest_totals(snap.upload_total, snap.download_total, now)
        seen: set[str] = set()
        user_open = 0
        for c in snap.connections:
            if not c.id:
                continue
            seen.add(c.id)
            f = self._flows.get(c.id)
            if f is None:
                user = not c.internal
                f = _Flow(now, user, c.exit, c.network == "tcp", c.dest_port, c.upload, c.download, not self._primed)
                self._flows[c.id] = f
                if user and self._primed:
                    self._user["new"] += 1
                    self._chain(f.chain)["new"] += 1
                    self._count(f, c.upload, c.download)
            elif f.user:
                self._count(f, max(0, c.upload - f.up), max(0, c.download - f.down))
                f.up, f.down = c.upload, c.download
            if f.user:
                user_open += 1
        self._user["conns"] = max(self._user["conns"], user_open)
        for cid in self._flows.keys() - seen:
            self._close(self._flows.pop(cid), now)
        self._primed = True

    def _close(self, f: _Flow, now: float) -> None:
        if not f.user:
            return
        self._user["closed"] += 1
        self._gone.append(f)
        if not f.tcp:
            return
        self._user["tcp_closed"] += 1
        if f.pre or f.up <= 0:
            return
        life = now - f.first
        if f.down == 0 and life < NORES_MAX_LIFE_S:
            self._user["nores"] += 1
            self._chain(f.chain)["nores"] += 1
        if f.down < EARLY_MAX_DOWN and life < EARLY_MAX_LIFE_S:
            self._user["early1k"] += 1

    def drain(self) -> dict:
        if self._frames == 0:
            self._new_minute()
            return {"frames": 0}
        flows = [f for f in (*self._flows.values(), *self._gone) if f.user]
        bulk = sorted(f.m_down / max(1, f.m_active) for f in flows if f.tcp and f.m_down >= BULK_MIN_BYTES)
        calls = [
            f
            for f in flows
            if not f.tcp
            and f.dport not in NON_CALL_UDP_PORTS
            and f.m_up >= CALL_MIN_BYTES
            and f.m_down >= CALL_MIN_BYTES
        ]
        quic = [f for f in flows if not f.tcp and f.dport == "443"]
        out = {
            "frames": self._frames,
            "up": self._up,
            "down": self._down,
            "pk_up": round(self._pk_up),
            "pk_down": round(self._pk_down),
            "user": dict(self._user),
            "bulk": {"n": len(bulk)}
            | ({"p50": round(_pct(bulk, 50)), "p90": round(_pct(bulk, 90)), "max": round(bulk[-1])} if bulk else {}),
            "chains": self._chains,
            "calls": {
                "n": len(calls),
                "meet": sum(1 for f in calls if f.dport in MEET_UDP_PORTS),
                "up": sum(f.m_up for f in calls),
                "down": sum(f.m_down for f in calls),
            },
            "quic": {"up": sum(f.m_up for f in quic), "down": sum(f.m_down for f in quic)},
        }
        for f in self._flows.values():
            f.m_up = f.m_down = f.m_active = 0
        self._new_minute()
        return out
