"""Old monitor vs the new verdict, minute by minute: where they disagree, so every difference can be explained."""

from collections import Counter
from collections.abc import Sequence

from app.services.health.model import BUSY, CHOPPY, DOWN, GOOD, NOT_MEASURED, POWER_CUT, SLOW, Minute

OLD_OF_NEW = {
    GOOD: "good",
    BUSY: "degraded",
    SLOW: "degraded",
    CHOPPY: "degraded",
    DOWN: "outage",
    POWER_CUT: "unknown",
    NOT_MEASURED: "unknown",
}
_SEVERITY = {"unknown": 0, "good": 1, "degraded": 2, "outage": 3}


def old_by_minute(rows: Sequence[tuple[int, int, str, str, str | None]], since: int, until: int) -> dict[int, str]:
    """The worst old status overlapping each minute, so a half-minute blip still counts."""
    out: dict[int, str] = {}
    for start, end, status, *_ in rows:
        first = max(start, since) // 60 * 60
        for ts in range(first, min(end, until - 1) + 1, 60):
            seen = out.get(ts)
            if seen is None or _SEVERITY[status] > _SEVERITY[seen]:
                out[ts] = status
    return out


def compare(minutes: Sequence[Minute], old_rows: Sequence[tuple[int, int, str, str, str | None]]) -> dict:
    if not minutes:
        return {"minutes": 0, "agree": 0, "pairs": {}, "disagreements": []}
    old = old_by_minute(old_rows, minutes[0].ts, minutes[-1].ts + 60)
    pairs: Counter[tuple[str, str]] = Counter()
    spans: list[dict] = []
    compared = agree = 0
    for m in minutes:
        was, now = old.get(m.ts), OLD_OF_NEW[m.state]
        if was is None or was == "unknown" or now == "unknown":
            continue
        compared += 1
        if was == now:
            agree += 1
            continue
        pairs[(was, m.state)] += 1
        last = spans[-1] if spans else None
        if last and last["old"] == was and last["new"] == m.state and last["end"] == m.ts:
            last["end"] = m.ts + 60
            last["minutes"] += 1
        else:
            spans.append(
                {
                    "start": m.ts,
                    "end": m.ts + 60,
                    "minutes": 1,
                    "old": was,
                    "new": m.state,
                    "cause": m.cause,
                    "why": list(m.why),
                }
            )
    return {
        "minutes": compared,
        "agree": agree,
        "pairs": {f"{a}->{b}": n for (a, b), n in pairs.items()},
        "disagreements": spans,
    }
