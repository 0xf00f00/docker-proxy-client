"""Builders for synthetic minute series: a healthy link by default, one knob per fault."""

from app.services.health import verdict
from app.services.health.baseline import Baseline
from app.services.health.model import Probe, Sample
from app.services.health.rules import Capacity

T0 = 1_800_000_000 // 60 * 60
CAP = Capacity(2_000_000.0, 400_000.0)
EDGES = {"route-a": "edge-a", "route-b": "edge-b"}
MATURE = Baseline(goodput_mbit=8.0, stun_rtt_p95=140.0, goodput_n=9, stun_n=40)
LEARNING = Baseline()


def edge(hello=3, fails=0, ack=None, unreach=0):
    ack = fails - unreach if ack is None else ack
    return {
        "hello": hello,
        "ack_to": ack,
        "dial_to": 0,
        "unreach": unreach,
        "dial_err": 0,
        "rst": fails - ack - unreach,
    }


def sample(
    i,
    *,
    boot="b",
    active="route-a",
    alive=None,
    edges=None,
    down=50_000,
    up=5_000,
    pk_down=100_000,
    new=3,
    calls=0,
    chains=None,
    frames=47,
    host=None,
    delay=130,
    paths=True,
    local=None,
    err=None,
):
    data = {
        "feed": {
            "frames": frames,
            "up": up,
            "down": down,
            "pk_up": 1000,
            "pk_down": pk_down,
            "user": {"new": new, "nores": 0},
            "calls": {"n": calls},
            "chains": {"route-a": {"down": down}} if chains is None else chains,
        },
        "edge": {"edge-a": edge(), "edge-b": edge(2), **(edges or {})},
        "host": {"temp": 62.0, **(host or {})},
    }
    if paths:
        data["paths"] = {
            "active": active,
            "alive": alive or {"route-a": True, "route-b": True, "naive": False},
            "delay": {active: delay},
        }
    if local is not None and paths:
        data["paths"]["local"] = local
    if err:
        data["err"] = err
    return Sample(T0 + 60 * i, boot, data)


def healthy(n, start=0, **kw):
    return [sample(start + i, **kw) for i in range(n)]


def probe(i, kind, ok=True, path="route-a", **data):
    return Probe(T0 + 60 * i + 5, kind, path, ok, 0, data)


def stun(i, ok=True, jitter=4.0, loss=0.0, rtt=135.0, path="route-a"):
    return probe(i, "stun", ok, path, recv=20 if ok else 0, loss=loss, jitter=jitter, rtt_p95=rtt if ok else None)


def goodput(i, ok=True, mbit=8.0, path="route-a"):
    return probe(i, "goodput", ok, path, mbit=mbit if ok else None)


def ladder(i, regime="normal", switch=None):
    return probe(
        i,
        "ladder",
        True,
        "route-a",
        regime=regime,
        intl_up=regime == "normal",
        **({"switch": switch} if switch else {}),
    )


def run(samples, probes=(), events=(), base=LEARNING, cap=CAP):
    return verdict.evaluate(samples, list(probes), list(events), cap, base, EDGES)


def states(r):
    return [m.state for m in r.minutes]


def changes(r):
    out, last = [], None
    for m in r.minutes:
        if m.state != last:
            out.append((m.ts - T0) // 60)
            out.append(m.state)
            last = m.state
    return out
