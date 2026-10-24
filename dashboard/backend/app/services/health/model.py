"""Row types the pure health rules work on; the engine builds them from stored rows."""

from dataclasses import dataclass, field

GOOD, BUSY, SLOW, CHOPPY, DOWN, POWER_CUT, NOT_MEASURED = (
    "good",
    "busy",
    "slow",
    "choppy",
    "down",
    "power_cut",
    "not_measured",
)


@dataclass(frozen=True, slots=True)
class Sample:
    ts: int
    boot: str
    data: dict


@dataclass(frozen=True, slots=True)
class Probe:
    ts: int
    kind: str
    path: str | None
    ok: bool
    bytes: int = 0
    data: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Event:
    ts: int
    boot: str
    kind: str
    data: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Minute:
    ts: int
    state: str
    cause: str
    why: tuple[str, ...] = ()
