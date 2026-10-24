"""Recorded minutes folded into states with hysteresis. I/O-free, so a replay and the live verdict run the same code."""

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median

from app.services.health import attribution, rules
from app.services.health.attribution import Facts
from app.services.health.baseline import Baseline
from app.services.health.model import (
    BUSY,
    CHOPPY,
    DOWN,
    GOOD,
    NOT_MEASURED,
    POWER_CUT,
    SLOW,
    Event,
    Minute,
    Probe,
    Sample,
)
from app.services.health.settings import MONITOR_GAP_S, PATH_EDGES

CLASH_LOCAL = frozenset({"DIRECT", "REJECT"})  # the controller's built-ins, for samples that predate "local"
DOWN_TRAFFIC_BYTES = 100_000
DOWN_IN, DOWN_OUT = 2, 5
EDGE_CLEAN_OUT = 10
BUSY_UTIL = 0.7
BUSY_IN, BUSY_WINDOW = 3, 5
PROOF_FRACTION = 0.5
PROOF_WINDOW_S = 3600
GOODPUT_SLOW_FRACTION = 0.25
GOODPUT_VALID_S = 36 * 3600
STUN_JITTER_MS, STUN_LOSS_PCT, STUN_RTT_FACTOR = 30.0, 2.0, 2.0
STUN_VALID_S = 5400
CALL_RECENT_S = 600
CALL_UP_BUSY = 0.85
SLOW_CHECK_MS, SLOW_CHECK_FACTOR = 1000, 4
HOT_C, HOT_THROTTLED_C = 80.0, 75.0
LADDER_VALID_S = 1800
RESTART_VALID_S = 600
REAL_TRAFFIC_NEW = 20
PASSIVE_SPAN_MIN = 10
DETAIL_MIN = 5
NORES_NOTE = 3

LABELS = {"browse": "Browsing", "download": "Downloads", "calls": "Calls"}


def _ev(signal: str, text: str, level: str = "info") -> dict:
    return {"signal": signal, "text": text, "level": level}


def classify_gap(prev: Sample, nxt: Sample, events: Sequence[Event]) -> tuple[str, str]:
    """What a hole in the minute series was. Same boot = the monitor wasn't running (not an
    outage); a new boot with no graceful stop marker = the power went."""
    if nxt.boot == prev.boot:
        return NOT_MEASURED, "monitor"
    boot = next(
        (e for e in events if e.kind == "boot" and e.boot == nxt.boot and e.data.get("prev_boot") == prev.boot), None
    )
    return (NOT_MEASURED, "restart") if boot and boot.data.get("graceful") else (POWER_CUT, "power_cut")


def is_gap(prev: Sample, nxt: Sample) -> bool:
    return nxt.boot != prev.boot or nxt.ts - prev.ts - 60 > MONITOR_GAP_S


class Tracker:
    def __init__(self, capacity: rules.Capacity, baseline: Baseline, edges: dict[str, str] | None = None) -> None:
        self.cap = capacity
        self.base = baseline
        self.edges = dict(PATH_EDGES if edges is None else edges)
        self.ts = 0
        self.data: dict = {}
        self._proof: tuple[int, float] | None = None
        self._goodput: Probe | None = None
        self._burst: Probe | None = None
        self._ladder: Probe | None = None
        self._stun: deque[Probe] = deque(maxlen=2)
        self._restarts: deque[int] = deque(maxlen=20)
        self._last_call: int | None = None
        self._call_busy: int | None = None
        self._delays: deque[int] = deque(maxlen=1440)
        self._passive: deque[int] = deque(maxlen=PASSIVE_SPAN_MIN)
        self._reset()

    def _reset(self) -> None:
        self._win: dict[str, deque[tuple[int, int]]] = {}
        self._detail: dict[str, deque[tuple[int, int, int, int]]] = {}
        self._clean: dict[str, int] = {}
        self._bad: dict[str, bool] = {}
        self._edge: dict[str, str] = {}
        self._down_on = False
        self._down_run = self._down_clear = 0
        self._busy: deque[bool] = deque(maxlen=BUSY_WINDOW)
        self._busy_on = False
        self._browse = NOT_MEASURED
        self._flowing = False

    def probe(self, p: Probe) -> None:
        if self._down_run and not p.ok and p.kind in ("stun", "goodput"):
            return  # no replies while every route is down is the outage, not a slow path or bad calls
        if p.kind == "stun":
            self._stun.append(p)
        elif p.kind == "goodput":
            self._goodput = p
        elif p.kind == "burst":
            self._burst = p
        elif p.kind == "ladder":
            self._ladder = p

    def event(self, e: Event) -> None:
        if e.kind == "container" and e.data.get("running"):
            self._restarts.append(e.ts)

    def gap(self, prev: Sample, nxt: Sample, events: Sequence[Event]) -> list[Minute]:
        state, cause = classify_gap(prev, nxt, events)
        self._reset()
        return [Minute(ts, state, cause, ("gap",)) for ts in range(prev.ts + 60, nxt.ts, 60)]

    def _edges_step(self, d: dict) -> None:
        for name, c in (d.get("edge") or {}).items():
            hello = c.get("hello", 0)
            fails = sum(v for k, v in c.items() if k != "hello")
            self._win.setdefault(name, deque(maxlen=rules.EDGE_WINDOW_MIN)).append((hello, fails))
            self._detail.setdefault(name, deque(maxlen=DETAIL_MIN)).append(
                (hello, fails, c.get("ack_to", 0), c.get("unreach", 0))
            )
            self._clean[name] = self._clean.get(name, 0) + 1 if fails == 0 else 0
            state = rules.edge_state(list(self._win[name]))
            bad = self._bad.get(name, False)
            if not bad:
                bad = state in ("poor", "fast")
            elif self._clean[name] >= EDGE_CLEAN_OUT:
                bad = False
                self._win[name].clear()
                state = "nodata"
            self._bad[name], self._edge[name] = bad, state

    def _down_step(self, paths: dict, feed: dict) -> None:
        alive = paths.get("alive") or {}
        local = frozenset(paths["local"]) if "local" in paths else CLASH_LOCAL
        proxied = sum(c.get("down", 0) for n, c in (feed.get("chains") or {}).items() if n not in local)
        self._flowing = proxied >= DOWN_TRAFFIC_BYTES
        cond = bool(alive) and all(v is False for v in alive.values()) and not self._flowing
        self._down_run = self._down_run + 1 if cond else 0
        self._down_clear = 0 if cond else self._down_clear + 1
        if not self._down_on and self._down_run >= DOWN_IN:
            self._down_on = True
        elif self._down_on and self._down_clear >= DOWN_OUT:
            self._down_on = False

    def _traffic_step(self, feed: dict, paths: dict) -> None:
        if feed.get("pk_down", 0) >= PROOF_FRACTION * self.cap.down_bps:
            self._proof = (self.ts, feed["pk_down"])
        calls = (feed.get("calls") or {}).get("n", 0) > 0
        if calls:
            self._last_call = self.ts
            if feed.get("up", 0) / 60 / self.cap.up_bps >= CALL_UP_BUSY:
                self._call_busy = self.ts
        full = bool(feed.get("frames")) and rules.utilization(feed, self.cap) >= BUSY_UTIL
        self._busy.append(full)
        if not self._busy_on:
            self._busy_on = sum(self._busy) >= BUSY_IN
        elif not any(self._busy):
            self._busy_on = False
        self._passive.append((feed.get("user") or {}).get("new", 0))
        delay = (paths.get("delay") or {}).get(paths.get("active"))
        if delay:
            self._delays.append(delay)

    def _primary(self) -> str | None:
        """The route the controller prefers: the first declared one in its failover order."""
        return next((n for n in (self.data.get("paths") or {}).get("alive") or {} if n in self.edges), None)

    def _active_inst(self) -> str | None:
        return self.edges.get((self.data.get("paths") or {}).get("active"))

    def _browse_bad(self) -> bool:
        inst = self._active_inst()
        return inst is not None and self._bad.get(inst, False)

    def _goodput_slow(self) -> bool:
        p = self._goodput
        if p is None or self.ts - p.ts > GOODPUT_VALID_S or self._proven() or (self._proof and self._proof[0] > p.ts):
            return False
        if not p.ok:
            return self.base.goodput_mbit is not None
        rate, ref = p.data.get("mbit"), self.base.goodput_mbit
        return rate is not None and ref is not None and rate < GOODPUT_SLOW_FRACTION * ref

    def _proven(self) -> bool:
        return self._proof is not None and self.ts - self._proof[0] <= PROOF_WINDOW_S

    def _stun_bad(self, p: Probe) -> bool:
        d = p.data
        ref = self.base.stun_rtt_p95
        return (
            not p.ok
            or (d.get("jitter") or 0) > STUN_JITTER_MS
            or (d.get("loss") or 0) > STUN_LOSS_PCT
            or (ref is not None and (d.get("rtt_p95") or 0) > STUN_RTT_FACTOR * ref)
        )

    def _calls(self) -> list[bool]:
        return [self._stun_bad(p) for p in self._stun if self.ts - p.ts <= STUN_VALID_S]

    def _choppy(self) -> bool:
        bad = self._calls()
        if len(bad) >= 2 and all(bad):
            return True
        recent = self._last_call is not None and self.ts - self._last_call <= CALL_RECENT_S
        return bool(bad) and bad[-1] and recent

    def _browse_calc(self) -> str:
        paths = self.data.get("paths") or {}
        alive = (paths.get("alive") or {}).get(paths.get("active"))
        if self._down_on:
            return DOWN
        if self._browse_bad():
            return SLOW
        if alive is False and not self._flowing:
            return self._browse  # a failed check not yet confirmed as an outage
        inst = self._active_inst()
        if inst is not None and self._edge.get(inst) in ("ok", "watch"):
            return GOOD
        return GOOD if alive or self._flowing else NOT_MEASURED

    def _restarted(self) -> bool:
        return any(self.ts - t <= RESTART_VALID_S for t in self._restarts)

    def _hot(self) -> bool:
        h = self.data.get("host") or {}
        temp, freq = h.get("temp"), h.get("freq")
        return temp is not None and (temp >= HOT_C or (freq is not None and freq < 1.0 and temp >= HOT_THROTTLED_C))

    def _regime(self) -> tuple[str | None, bool | None]:
        p = self._ladder
        if p is None or self.ts - p.ts > LADDER_VALID_S or not p.ok:
            return None, None
        regime = p.data.get("regime")
        switch = p.data.get("switch")
        return (None if regime == "unknown" else regime), (
            any(v is not None for v in switch.values()) if switch is not None else None
        )

    def _facts(self, state: str) -> Facts:
        paths = self.data.get("paths") or {}
        active = paths.get("active")
        regime, helps = self._regime()
        alive = paths.get("alive") or {}
        edges = {
            n: (sum(a for a, *_ in w), sum(f for _, f, *_ in w), sum(k for _, _, k, _ in w))
            for n, w in self._detail.items()
        }
        standby = [p for p in self.edges if p != active]
        return Facts(
            state=state,
            regime=regime,
            all_dead=self._down_on,
            edges=edges,
            unreach=sum(u for w in self._detail.values() for *_, u in w),
            standby_alive=any(
                alive.get(p) is True and not self._bad.get(self.edges[p], False) for p in standby if p in self.edges
            ),
            switch_helps=helps,
            restarted=self._restarted(),
            hot=self._hot(),
            browse_bad=self._browse_bad(),
            call_busy=self._call_busy is not None and self.ts - self._call_busy <= CALL_RECENT_S,
        )

    def _weak(self) -> tuple[str, ...]:
        """Faint signals on a good minute, kept so a disagreement with another monitor can be explained."""
        out = []
        inst = self._active_inst()
        if inst in self._detail and self._detail[inst][-1][1]:
            hello, fails, *_ = self._detail[inst][-1]
            out.append(f"edge_fail:{fails}/{hello}")
        nores = ((self.data.get("feed") or {}).get("user") or {}).get("nores", 0)
        if nores >= NORES_NOTE:
            out.append(f"nores:{nores}")
        active, primary = (self.data.get("paths") or {}).get("active"), self._primary()
        if active and primary and active != primary:
            out.append("backup_route")
        return tuple(out)

    def _state(self) -> tuple[str, tuple[str, ...]]:
        browse = self._browse
        why: list[str] = []
        if browse == DOWN:
            return DOWN, ("all_paths_dead",)
        if browse == SLOW:
            why.append("edge_bad")
        if self._goodput_slow():
            why.append("goodput_slow")
        if why:
            return SLOW, tuple(why)
        if self._choppy():
            return CHOPPY, ("calls_bad",)
        if self._busy_on:
            return BUSY, ("link_full",)
        if browse == NOT_MEASURED:
            return NOT_MEASURED, ("browse_unknown",)
        return GOOD, self._weak()

    def step(self, s: Sample) -> Minute:
        self.ts, self.data = s.ts, s.data
        feed, paths = s.data.get("feed") or {}, s.data.get("paths") or {}
        self._edges_step(s.data)
        if not feed.get("frames") and not paths:
            self._passive.append(0)
            return Minute(s.ts, NOT_MEASURED, "feed", ("blind",))
        self._down_step(paths, feed)
        self._traffic_step(feed, paths)
        self._browse = self._browse_calc()
        state, why = self._state()
        cause = "feed" if state == NOT_MEASURED else attribution.attribute(self._facts(state))[0]
        return Minute(s.ts, state, cause, why)

    def _passive_traffic(self) -> bool:
        return sum(self._passive) >= REAL_TRAFFIC_NEW

    def _confidence(self) -> str:
        if self._passive_traffic():
            return "real traffic"
        return "probe only" if self.base.ready else "learning"

    def _usual(self, ref: float | None, unit: str) -> str:
        return f" (usual {ref:g} {unit})" if ref is not None else ""

    def _edge_evidence(self) -> list[dict]:
        out = []
        active = self._active_inst()
        for name, w in self._win.items():
            attempts = sum(a for a, _ in w)
            fails = sum(f for _, f in w)
            state = self._edge.get(name, "nodata")
            role = "active" if name == active else "backup"
            if state == "nodata":
                out.append(_ev("edge", f"{name} ({role}): too few handshakes to judge ({attempts} in 30 min)"))
                continue
            level = {"poor": "bad", "fast": "bad", "watch": "warn"}.get(state, "info")
            if role == "backup" and level == "bad":
                level = "warn"
            out.append(_ev("edge", f"{name} ({role}): {fails} of {attempts} handshakes failed in 30 min", level))
        return out

    def _path_evidence(self) -> list[dict]:
        paths = self.data.get("paths") or {}
        alive = paths.get("alive") or {}
        out = []
        if alive:
            up = sorted(n for n, v in alive.items() if v)
            level = "bad" if self._down_on else "info"
            out.append(_ev("paths", f"Paths passing their own check: {', '.join(up) or 'none'}", level))
        active, primary = paths.get("active"), self._primary()
        if active and primary and active != primary:
            out.append(_ev("paths", f"Backup route in use: {active}", "warn"))
        delay = (paths.get("delay") or {}).get(active)
        if delay and len(self._delays) >= 30:
            usual = median(self._delays)
            if delay > max(SLOW_CHECK_MS, SLOW_CHECK_FACTOR * usual):
                out.append(_ev("paths", f"Active path check took {delay} ms (usual {usual:.0f})", "warn"))
        return out

    def _download_evidence(self) -> list[dict]:
        out = []
        if self._proven() and self._proof is not None:
            out.append(_ev("download", f"Reached {self._proof[1] * 8 / 1e6:.1f} Mbit {self._ago(self._proof[0])}"))
        else:
            out.append(_ev("download", "No proof of full speed in the last 60 minutes"))
        p = self._goodput
        if p is not None and self.ts - p.ts <= GOODPUT_VALID_S:
            slow = self._goodput_slow()
            usual = self._usual(self.base.goodput_mbit, "Mbit")
            if p.ok:
                out.append(
                    _ev(
                        "goodput",
                        f"Test download {p.data.get('mbit')} Mbit{usual} {self._ago(p.ts)}",
                        "bad" if slow else "info",
                    )
                )
            else:
                out.append(_ev("goodput", f"Test download failed {self._ago(p.ts)}", "bad" if slow else "warn"))
        return out

    def _calls_evidence(self) -> list[dict]:
        out = []
        if self._stun:
            p = self._stun[-1]
            if self.ts - p.ts <= STUN_VALID_S:
                d = p.data
                bad = self._stun_bad(p)
                text = (
                    f"Call test: jitter {d.get('jitter')} ms, loss {d.get('loss')}%, RTT p95 {d.get('rtt_p95')} ms"
                    f"{self._usual(self.base.stun_rtt_p95, 'ms')} {self._ago(p.ts)}"
                    if p.ok
                    else f"Call test got no replies {self._ago(p.ts)}"
                )
                out.append(_ev("calls", text, "warn" if bad else "info"))
        if self._call_busy is not None and self.ts - self._call_busy <= CALL_RECENT_S:
            out.append(_ev("calls", "The upload link was nearly full during a call", "warn"))
        return out

    def _ago(self, ts: int) -> str:
        mins = max(0, (self.ts + 60 - ts) // 60)
        return f"{mins} min ago" if mins < 120 else f"{mins // 60} h ago"

    def _misc_evidence(self) -> list[dict]:
        feed = self.data.get("feed") or {}
        out = []
        if feed.get("frames"):
            down = feed.get("down", 0) / 60 / self.cap.down_bps
            up = feed.get("up", 0) / 60 / self.cap.up_bps
            out.append(
                _ev(
                    "link",
                    f"Link in use: {down:.0%} down, {up:.0%} up",
                    "warn" if max(down, up) >= BUSY_UTIL else "info",
                )
            )
        h = self.data.get("host") or {}
        if h.get("temp") is not None:
            out.append(_ev("host", f"Device temperature {h['temp']:.0f} °C", "warn" if self._hot() else "info"))
        b = self._burst
        if b is not None and self.ts - b.ts <= 3600:
            d = b.data
            out.append(
                _ev(
                    "burst",
                    f"Handshake test: {d.get('failed')} of {d.get('n')} failed {self._ago(b.ts)}",
                    "bad" if not b.ok else "info",
                )
            )
        regime, helps = self._regime()
        if regime is not None:
            out.append(_ev("regime", f"Outside check: {regime}"))
        if helps is not None:
            out.append(
                _ev("switch", "A backup route works" if helps else "No backup route works", "info" if helps else "warn")
            )
        if self._restarted():
            out.append(_ev("restart", "A proxy component restarted in the last 10 minutes", "warn"))
        return out

    def config_issues(self) -> list[str]:
        """Where the declared route/edge topology no longer matches what is running."""
        paths, err = self.data.get("paths") or {}, self.data.get("err") or []
        out = [] if self.edges else ["no routes are mapped to edge instances"]
        if paths.get("alive"):
            out += [f"route {r} is not known to the proxy controller" for r in self.edges if r not in paths["alive"]]
        out += [f"cannot read edge {e}'s log" for e in dict.fromkeys(self.edges.values()) if f"edge:{e}" in err]
        return out

    def evidence(self) -> list[dict]:
        return [
            *self._path_evidence(),
            *self._edge_evidence(),
            *self._download_evidence(),
            *self._calls_evidence(),
            *self._misc_evidence(),
        ]

    def activities(self) -> list[dict]:
        browse = self._browse
        bad = self._calls()
        if self._choppy():
            calls = (CHOPPY, "Recent call tests were poor")
        elif bad:
            calls = (GOOD, "Recent call test was fine")
        else:
            calls = (NOT_MEASURED, "No recent call test")
        if self._goodput_slow():
            download = (SLOW, "The last test download was slow")
        elif self._busy_on:
            download = (BUSY, "The link is full")
        elif self._proven():
            download = (GOOD, "Full speed reached recently")
        elif self._goodput is not None and self.ts - self._goodput.ts <= GOODPUT_VALID_S and self._goodput.ok:
            download = (GOOD, "The last test download was fine")
        else:
            download = (NOT_MEASURED, "Not enough recent evidence")
        detail = {
            DOWN: "Every route is failing",
            SLOW: "Connections are failing more than usual",
            NOT_MEASURED: "Cannot tell",
        }
        return [
            {
                "key": "browse",
                "label": LABELS["browse"],
                "state": browse,
                "detail": detail.get(browse, "Connections are succeeding"),
            },
            {"key": "download", "label": LABELS["download"], "state": download[0], "detail": download[1]},
            {"key": "calls", "label": LABELS["calls"], "state": calls[0], "detail": calls[1]},
        ]

    def verdict(self, now: float) -> dict:
        """The full current verdict; a series that stopped growing reads Not measured, never green."""
        if not self.ts or now - self.ts > 60 + MONITOR_GAP_S:
            return _unmeasured("monitor", self.ts)
        if not (self.data.get("feed") or {}).get("frames") and not self.data.get("paths"):
            return _unmeasured("feed", self.ts)
        state, _ = self._state()
        cause, label = attribution.attribute(self._facts(state))
        if state == NOT_MEASURED:
            cause, label = "feed", attribution.LABELS["feed"]
        return {
            "ts": self.ts,
            "state": state,
            "cause": cause,
            "label": label,
            "confidence": self._confidence(),
            "activities": self.activities(),
            "evidence": self.evidence(),
            "config": self.config_issues(),
        }


def _unmeasured(cause: str, ts: int) -> dict:
    return {
        "ts": ts,
        "state": NOT_MEASURED,
        "cause": cause,
        "label": attribution.LABELS[cause],
        "confidence": "probe only",
        "activities": [],
        "evidence": [],
        "config": [],
    }


@dataclass(slots=True)
class Run:
    minutes: list[Minute]
    tracker: Tracker


def evaluate(
    samples: Sequence[Sample],
    probes: Sequence[Probe],
    events: Sequence[Event],
    capacity: rules.Capacity,
    baseline: Baseline,
    edges: dict[str, str] | None = None,
) -> Run:
    """Fold time-ordered inputs into one Minute per recorded or missing minute."""
    tracker = Tracker(capacity, baseline, edges)
    minutes: list[Minute] = []
    prev: Sample | None = None
    pi = ei = 0
    for s in samples:
        if prev is not None and is_gap(prev, s):
            minutes += tracker.gap(prev, s, events)
        end = s.ts + 60
        while pi < len(probes) and probes[pi].ts < end:
            tracker.probe(probes[pi])
            pi += 1
        while ei < len(events) and events[ei].ts < end:
            tracker.event(events[ei])
            ei += 1
        minutes.append(tracker.step(s))
        prev = s
    return Run(minutes, tracker)


def segments(minutes: Sequence[Minute]) -> list[dict]:
    """Consecutive minutes with the same state and cause as [start, end) spans."""
    out: list[dict] = []
    for m in minutes:
        if out and out[-1]["state"] == m.state and out[-1]["cause"] == m.cause and out[-1]["end"] == m.ts:
            out[-1]["end"] = m.ts + 60
        else:
            out.append({"start": m.ts, "end": m.ts + 60, "state": m.state, "cause": m.cause})
    return out
