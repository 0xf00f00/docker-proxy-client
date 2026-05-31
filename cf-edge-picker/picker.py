#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
import os
import re
import socket
import time
from pathlib import Path

import docker

log = logging.getLogger("picker")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


# --- config (env-overridable) ------------------------------------------------
POOL_FILE = Path(os.environ.get("POOL_FILE", "/pool/pool.txt"))
COREDNS_HOSTS = Path(os.environ.get("COREDNS_HOSTS", "/coredns-fallback/edge.hosts"))
SNISPOOF_CONF = Path(os.environ.get("SNISPOOF_CONF", "/snispoof2/config.ini"))
SNISPOOF_CONTAINER = os.environ.get("SNISPOOF_CONTAINER", "sni-spoofing-fallback")
STATE_FILE = Path(os.environ.get("STATE_FILE", "/state/picker.json"))
FRONT_HOST = os.environ.get("FRONT_HOST", "")
PORT = _int("PORT", 443)
PING_COUNT = _int("PING_COUNT", 10)          # probes per IP (loss resolution)
KEEP_MAX = _float("KEEP_MAX", 0.20)          # keep a path if loss <= this fraction
PICK_MAX = _float("PICK_MAX", 0.10)          # a replacement edge must be this clean
MAX_CANDIDATES = _int("MAX_CANDIDATES", 3)   # pool IPs to test per degraded path
BASE_INTERVAL = _int("BASE_INTERVAL", 600)   # normal cadence between checks (s)
MAX_BACKOFF = _int("MAX_BACKOFF", 21600)     # cap backoff during long outages (6h)
MIN_RESTART_GAP = _int("MIN_RESTART_GAP", 300)  # min seconds between snispoof restarts
CONNECT_TIMEOUT = _float("CONNECT_TIMEOUT", 2.0)
SLEEP_CAP = _int("SLEEP_CAP", 300)           # max single sleep (keeps stop responsive)
QUARANTINE_TTL = _int("QUARANTINE_TTL", 3600)  # skip an in-use IP that went bad this long

_CONNECT_RE = re.compile(r"^(\s*connect\s*=\s*).*$", re.MULTILINE)


# --- state -------------------------------------------------------------------
def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"fails": 0, "next_run": 0, "last_restart": 0, "quarantine": {}}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state))


# --- probes / parsing --------------------------------------------------------
def tcp_loss(ip: str) -> float:
    """Fraction of PING_COUNT raw TCP connects to ip:PORT that failed.

    Pure TCP reachability -- no TLS -- which is exactly the censorship/throttle
    signal we care about (the worker SNI is hidden by the evasion layer).
    """
    miss = 0
    for _ in range(PING_COUNT):
        try:
            socket.create_connection((ip, PORT), timeout=CONNECT_TIMEOUT).close()
        except OSError:
            miss += 1
    return miss / PING_COUNT


def read_pool() -> list[str]:
    try:
        lines = POOL_FILE.read_text().splitlines()
    except FileNotFoundError:
        return []
    return [s for ln in lines if (s := ln.strip()) and not s.startswith("#")]


def current_byedpi() -> str | None:
    try:
        for ln in COREDNS_HOSTS.read_text().splitlines():
            s = ln.strip()
            if s and not s.startswith("#"):
                return s.split()[0]
    except FileNotFoundError:
        pass
    return None


def current_snispoof() -> str | None:
    try:
        m = re.search(r"^\s*connect\s*=\s*([^:\s]+)", SNISPOOF_CONF.read_text(), re.MULTILINE)
    except FileNotFoundError:
        return None
    return m.group(1) if m else None


# --- apply -------------------------------------------------------------------
def apply_byedpi(ip: str) -> None:
    if not FRONT_HOST:
        log.error("FRONT_HOST unset; cannot manage byedpi path")
        return
    COREDNS_HOSTS.write_text(f"{ip} {FRONT_HOST}\n")
    log.info("byedpi path -> %s (coredns hosts rewritten; no restart)", ip)


def apply_snispoof(ip: str, state: dict, client) -> None:
    text = SNISPOOF_CONF.read_text()
    SNISPOOF_CONF.write_text(_CONNECT_RE.sub(rf"\g<1>{ip}:{PORT}", text))
    log.info("snispoof path -> %s (config rewritten)", ip)
    now = int(time.time())
    waited = now - state["last_restart"]
    if waited >= MIN_RESTART_GAP:
        if restart_container(client, SNISPOOF_CONTAINER):
            state["last_restart"] = now
    else:
        log.info("restart of %s rate-limited (%ds left); config staged for next restart",
                 SNISPOOF_CONTAINER, MIN_RESTART_GAP - waited)


def restart_container(client, name: str) -> bool:
    if client is None:
        log.warning("no docker client; cannot restart %s (new IP applies on its next restart)", name)
        return False
    try:
        client.containers.get(name).restart(timeout=10)
        log.info("restarted %s", name)
        return True
    except Exception as exc:  # docker errors, not found, etc. -- never fatal
        log.warning("restart %s failed: %s; new IP applies on its next restart", name, exc)
        return False


def pick_clean(pool: list[str], exclude: str | None, quarantine: dict, now: int) -> str | None:
    """First pool IP (best-first) that is clean (<= PICK_MAX) and != exclude.

    Quarantined IPs are skipped, but never to the point of starving the pool: if
    every candidate is quarantined we ignore it and reconsider them all.
    """
    candidates = [ip for ip in pool if ip != exclude]
    available = [ip for ip in candidates if quarantine.get(ip, 0) <= now] or candidates
    for ip in available[:MAX_CANDIDATES]:
        loss = tcp_loss(ip)
        if loss <= PICK_MAX:
            log.info("candidate %s loss=%.0f%% OK", ip, loss * 100)
            return ip
        log.info("candidate %s loss=%.0f%% rejected", ip, loss * 100)
    return None


def _settle(path_name: str, current: str | None, pool: list[str], other: str | None,
            quarantine: dict, now: int, apply, *apply_extra) -> tuple[str | None, bool]:
    """Return (ip_in_use, healthy) for one path, repointing it if degraded.

    A currently-applied IP that goes bad is quarantined (only confirmed in-use
    failures, so transient candidate blips never lock an IP out).
    """
    if current and tcp_loss(current) <= KEEP_MAX:
        quarantine.pop(current, None)
        log.info("%s path healthy (%s)", path_name, current)
        return current, True
    log.info("%s path degraded/unset (%s); seeking replacement", path_name, current)
    if current:
        quarantine[current] = now + QUARANTINE_TTL
        log.info("quarantined %s for %ds", current, QUARANTINE_TTL)
    new = pick_clean(pool, other, quarantine, now)
    if new:
        apply(new, *apply_extra)
        return new, True
    log.info("no clean candidate for %s path; leaving %s", path_name, current)
    return current, False


# --- one tick; returns seconds until the next due run ------------------------
def tick(client) -> int:
    state = load_state()
    now = int(time.time())
    if now < state["next_run"]:
        return state["next_run"] - now

    pool = read_pool()
    if not pool:
        log.info("pool empty/missing (%s) -- keeping current edges", POOL_FILE)
        state["next_run"] = now + BASE_INTERVAL
        save_state(state)
        return BASE_INTERVAL

    quarantine = {ip: exp for ip, exp in state.setdefault("quarantine", {}).items() if exp > now}
    state["quarantine"] = quarantine

    bip, sip = current_byedpi(), current_snispoof()
    log.info("current: byedpi=%s snispoof=%s; pool=%s", bip, sip, pool)

    bip, b_ok = _settle("byedpi", bip, pool, sip, quarantine, now, apply_byedpi)
    sip, s_ok = _settle("snispoof", sip, pool, bip, quarantine, now, apply_snispoof, state, client)
    healthy = int(b_ok) + int(s_ok)

    # distinct-IP guard: the two paths must not share fate.
    if bip and bip == sip:
        log.warning("both paths on %s; trying to diversify snispoof", bip)
        new = pick_clean(pool, bip, quarantine, now)
        if new:
            apply_snispoof(new, state, client)
        else:
            log.warning("pool lacks a 2nd distinct clean IP; leaving both on %s", bip)

    if healthy >= 1:
        state["fails"] = 0
        delay = BASE_INTERVAL
        log.info("ok: %d/2 path(s) healthy; next check in %ds", healthy, delay)
    else:
        state["fails"] += 1
        delay = min(BASE_INTERVAL * (2 ** min(state["fails"], 16)), MAX_BACKOFF)
        log.info("outage: 0/2 healthy and no clean candidates; NO restarts; "
                 "backing off %ds (fails=%d)", delay, state["fails"])
    state["next_run"] = now + delay
    save_state(state)
    return delay


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)sZ picker: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    try:
        client = docker.from_env()
        client.ping()
    except Exception as exc:
        log.warning("docker unavailable (%s); will run but cannot restart %s",
                    exc, SNISPOOF_CONTAINER)
        client = None
    log.info("picker started; front=%s container=%s base=%ds max_backoff=%ds",
             FRONT_HOST, SNISPOOF_CONTAINER, BASE_INTERVAL, MAX_BACKOFF)
    while True:
        try:
            due = tick(client)
        except Exception:
            log.exception("tick failed; retrying after %ds", BASE_INTERVAL)
            due = BASE_INTERVAL
        time.sleep(max(1, min(due, SLEEP_CAP)))


if __name__ == "__main__":
    main()
