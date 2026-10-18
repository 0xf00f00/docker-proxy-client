"""Host health and identity from /sys and /proc."""

import ctypes
import ctypes.util
import sys
import time
import uuid
from pathlib import Path

_FALLBACK_BOOT_ID = f"dev-{uuid.uuid4()}"
_STA_UNSYNC = 0x0040
_TIME_ERROR = 5


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _num(path: Path) -> float | None:
    text = _read(path)
    try:
        return float(text) if text is not None else None
    except ValueError:
        return None


def boot_id(root: Path = Path("/")) -> str:
    return _read(root / "proc/sys/kernel/random/boot_id") or _FALLBACK_BOOT_ID


def uptime(root: Path = Path("/")) -> float:
    text = _read(root / "proc/uptime")
    return float(text.split()[0]) if text else time.monotonic()


class _Timex(ctypes.Structure):
    _fields_ = [
        ("modes", ctypes.c_uint),
        ("offset", ctypes.c_long),
        ("freq", ctypes.c_long),
        ("maxerror", ctypes.c_long),
        ("esterror", ctypes.c_long),
        ("status", ctypes.c_int),
        ("_rest", ctypes.c_byte * 256),
    ]


def clock_synced() -> bool:
    """Whether the kernel clock is NTP-synced; without an RTC, wall time is unreliable until it is."""
    if not sys.platform.startswith("linux"):
        return True
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
        tx = _Timex()
        state = libc.adjtimex(ctypes.byref(tx))
    except (OSError, AttributeError):
        return True
    return state >= 0 and state != _TIME_ERROR and not tx.status & _STA_UNSYNC


def _hwmon(root: Path, name: str) -> Path | None:
    for d in sorted((root / "sys/class/hwmon").glob("hwmon*")):
        if _read(d / "name") == name:
            return d
    return None


def read(root: Path = Path("/"), iface: str = "eth0") -> dict:
    out: dict = {}
    temp = _num(root / "sys/class/thermal/thermal_zone0/temp")
    if temp is not None:
        out["temp"] = round(temp / 1000, 1)
    cpu = root / "sys/devices/system/cpu/cpu0/cpufreq"
    cur, top = _num(cpu / "scaling_cur_freq"), _num(cpu / "cpuinfo_max_freq")
    if cur and top:
        out["freq"] = round(cur / top, 2)
    volt = _hwmon(root, "rpi_volt")
    if volt is not None:
        alarm = _num(volt / "in0_lcrit_alarm")
        if alarm is not None:
            out["uv"] = int(alarm)
    load = _read(root / "proc/loadavg")
    if load:
        out["load1"] = float(load.split()[0])
    for line in (_read(root / "proc/meminfo") or "").splitlines():
        if line.startswith("MemAvailable:"):
            out["mem_mb"] = int(line.split()[1]) // 1024
            break
    stats = root / "sys/class/net" / iface / "statistics"
    rx, tx = _num(stats / "rx_bytes"), _num(stats / "tx_bytes")
    if rx is not None and tx is not None:
        out["nic"] = (int(rx), int(tx))
    return out
