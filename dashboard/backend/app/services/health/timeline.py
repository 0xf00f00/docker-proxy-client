"""Pure analytics over stored health segments: live tail (``clip_segments``), buckets and totals
(``bucketize``, ``summarize``), and discrete incidents (``find_incidents``)."""

from collections import Counter

from app.services.health.cause import cause_for, describe_detail
from app.services.health.settings import MAX_GAP_S, STATUSES


def clip_segments(
    rows: list[tuple[int, int, str, str, str | None]], until: int, alive_ts: int | None
) -> list[tuple[int, int, str]]:
    """Stored segments -> ``(start, end, status)``. Only the tail needs fixing: the open row extends to
    ``alive_ts``, and a span past it wider than ``MAX_GAP_S`` (monitor down) becomes ``unknown``."""
    segs: list[list] = [[s, e, status] for s, e, status, _regime, _detail in rows]
    if not segs:
        return []
    edge = segs[-1][1]
    if alive_ts is not None and alive_ts > edge:
        segs[-1][1] = edge = alive_ts
    if until > edge:
        if until - edge > MAX_GAP_S:
            segs.append([edge, until, "unknown"])
        else:
            segs[-1][1] = until
    return [(a, b, st) for a, b, st in segs if b > a]


def bucketize(segments: list[tuple[int, int, str]], since: int, until: int, size: int) -> list[dict]:
    """Aggregate segments into fixed-width buckets, each summarizing time-in-state
    and a glanceable worst-case status (the color the bar/cell shows)."""
    count = max(1, (until - since + size - 1) // size)
    secs = [{s: 0 for s in STATUSES} for _ in range(count)]
    for seg_start, seg_end, status in segments:
        # Clip the segment into each bucket it spans.
        a = max(seg_start, since)
        b = min(seg_end, until)
        while a < b:
            idx = (a - since) // size
            if idx < 0 or idx >= count:
                break
            bucket_end = since + (idx + 1) * size
            chunk_end = min(b, bucket_end)
            secs[idx][status] += chunk_end - a
            a = chunk_end
    return [_bucket_summary(since + i * size, size, secs[i]) for i in range(count)]


# Fraction of measured time that must be bad before a bucket shows trouble: ≥ this much
# outage paints it red, ≥ this much outage-or-degraded paints it amber, else healthy.
# Tune outage/degraded independently by splitting this into two constants if needed.
_TROUBLE_FRACTION = 0.05


def _bucket_summary(ts: int, size: int, secs: dict[str, int]) -> dict:
    measured = secs["good"] + secs["degraded"] + secs["outage"]
    total = measured + secs["unknown"]
    if measured == 0:
        status = "unknown"
        uptime = None
    else:
        outage_frac = secs["outage"] / measured
        trouble_frac = (secs["outage"] + secs["degraded"]) / measured
        if outage_frac >= _TROUBLE_FRACTION:
            status = "outage"
        elif trouble_frac >= _TROUBLE_FRACTION:
            status = "degraded"
        else:
            status = "good"
        uptime = round(secs["good"] / measured * 100, 1)
    return {
        "ts": ts,
        "size": size,
        "status": status,
        "uptimePct": uptime,
        "secs": secs,
        "measuredSecs": measured,
        "totalSecs": total,
    }


def summarize(segments: list[tuple[int, int, str]], since: int, until: int) -> dict:
    """Window totals: seconds per state (clamped to [since, until]) + uptime %."""
    secs = {s: 0 for s in STATUSES}
    for start, end, status in segments:
        a, b = max(start, since), min(end, until)
        if b > a:
            secs[status] += b - a
    measured = secs["good"] + secs["degraded"] + secs["outage"]
    return {
        "secs": secs,
        "measuredSecs": measured,
        "uptimePct": round(secs["good"] / measured * 100, 1) if measured else None,
    }


def dominant_regime(run: list[tuple[int, int, str, str, str | None]]) -> str:
    """The regime holding the most outage time, so a long incident is named for what lasted, not what came first."""
    secs: Counter[str] = Counter()
    for s, e, status, regime, _detail in run:
        if status == "outage":
            secs[regime] += max(e - s, 1)
    return secs.most_common(1)[0][0] if secs else run[-1][3]


def find_incidents(
    rows: list[tuple[int, int, str, str, str | None]], until: int, alive_ts: int | None, min_duration_s: int = 60
) -> list[dict]:
    """Incidents = maximal runs of outage/degraded segments, each with its worst status, best-explaining regime,
    a plain-language cause and an optional finer ``causeDetail``. Runs under ``min_duration_s`` are flaps."""
    incidents: list[dict] = []
    run: list[tuple[int, int, str, str, str | None]] = []

    def close(recovery_ts: int, ongoing: bool) -> None:
        if not run:
            return
        start = run[0][0]
        duration = recovery_ts - start
        if duration < min_duration_s:
            return
        worst = "outage" if any(seg[2] == "outage" for seg in run) else "degraded"
        regime = dominant_regime(run)
        kind, label = cause_for(worst, regime)
        # The "why": take the onset detail of the first segment at the worst status.
        detail = next((seg[4] for seg in run if seg[2] == worst), None)
        incidents.append(
            {
                "start": start,
                "end": None if ongoing else recovery_ts,
                "durationS": duration,
                "status": worst,
                "regime": regime,
                "cause": kind,
                "causeLabel": label,
                "causeDetail": describe_detail(worst, regime, detail),
                "ongoing": ongoing,
            }
        )

    for s, e, status, regime, detail in rows:
        if status in ("outage", "degraded"):
            run.append((s, e, status, regime, detail))
        else:
            # A good/unknown segment ends the run; it recovered where that segment began.
            close(run[-1][1] if run else 0, ongoing=False)
            run.clear()
    if run:
        # Trouble at the live edge. If the monitor is still measuring it's ongoing;
        # if it went dark (gap > MAX_GAP) we stop at the last-alive edge, state unknown.
        edge = alive_ts if (alive_ts is not None and alive_ts > run[-1][1]) else run[-1][1]
        if until - edge <= MAX_GAP_S:
            close(max(edge, until), ongoing=True)
        else:
            close(edge, ongoing=False)
    return incidents
