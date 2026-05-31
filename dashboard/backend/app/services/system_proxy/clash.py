"""Clash implementation of the SystemProxyController interface.

Maps the generic mode names to Clash's terminology:
    auto    <-> clash "rule" mode + the "auto" proxy-group
    manual  <-> clash "global" mode + the "GLOBAL" proxy-group
"""

import asyncio
import io
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import httpx
from ruamel.yaml import YAML

from app.config import settings
from app.models.schemas import (
    SystemProxyReorderResult,
    SystemProxyRoute,
    SystemProxyState,
)
from app.services import docker_service
from app.services.system_proxy import registry
from app.services.system_proxy.base import SystemProxyController

# Names Clash exposes that aren't user-selectable routes.
HIDDEN_ROUTES = frozenset({"REJECT", "DIRECT", "auto", "direct_interface"})

_CLASH_CONFIG_CANDIDATES = [
    Path(settings.configs_base_path) / "clash" / "config.yaml",
    Path.cwd() / "clash" / "config.yaml",
    Path.cwd().parent.parent / "clash" / "config.yaml",
]

_MODE_TO_CLASH = {"auto": "rule", "manual": "global"}
_CLASH_TO_MODE = {v: k for k, v in _MODE_TO_CLASH.items()}


@dataclass
class ClashController(SystemProxyController):
    container_name: str

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if settings.clash_api_secret:
            h["Authorization"] = f"Bearer {settings.clash_api_secret}"
        return h

    async def _get_clash_mode(self) -> str:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{settings.clash_api_url}/configs", headers=self._headers())
            r.raise_for_status()
            return r.json().get("mode", "rule")

    async def _get_clash_group(self, group: str) -> dict:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{settings.clash_api_url}/proxies/{group}", headers=self._headers())
            r.raise_for_status()
            return r.json()

    async def get_state(self) -> SystemProxyState:
        clash_mode = await self._get_clash_mode()
        mode = _CLASH_TO_MODE.get(clash_mode, "auto")
        group_name = "auto" if mode == "auto" else "GLOBAL"
        group = await self._get_clash_group(group_name)
        visible = [n for n in (group.get("all") or []) if n not in HIDDEN_ROUTES]
        return SystemProxyState(
            mode=mode,
            routes=[SystemProxyRoute(name=n) for n in visible],
            active=group.get("now"),
            reorderable=(mode == "auto"),
        )

    async def set_mode(self, mode: str) -> None:
        clash_mode = _MODE_TO_CLASH.get(mode)
        if clash_mode is None:
            raise ValueError(f"Unknown mode: {mode!r}")
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.patch(
                f"{settings.clash_api_url}/configs",
                json={"mode": clash_mode},
                headers=self._headers(),
            )
            r.raise_for_status()

    async def switch(self, name: str) -> None:
        # Switching only makes sense in manual (= clash global) mode.
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.put(
                f"{settings.clash_api_url}/proxies/GLOBAL",
                json={"name": name},
                headers=self._headers(),
            )
            r.raise_for_status()

    async def reorder(self, routes: list[str]) -> SystemProxyReorderResult:
        if not routes:
            raise ValueError("routes must be non-empty")
        await asyncio.to_thread(_reorder_in_yaml, "auto", routes)
        await asyncio.to_thread(docker_service.restart_container, self.container_name)
        await _wait_for_group_ready("auto", self._headers())
        await _trigger_group_test("auto", self._headers())
        try:
            group = await self._get_clash_group("auto")
            active = group.get("now")
        except Exception:
            active = None
        return SystemProxyReorderResult(success=True, routes=routes, active=active)

    async def stream_traffic(self) -> AsyncIterator[tuple[int, int]]:
        """Stream Clash's ``/traffic`` endpoint as (up, down) bytes/sec samples.

        Clash owns the TUN interface, so this is the ground-truth rate for the
        whole system proxy. Clash emits one newline-delimited JSON object per
        second. The stream is held open until Clash closes it or errors.
        """
        url = f"{settings.clash_api_url}/traffic"
        async with (
            httpx.AsyncClient(timeout=None, headers=self._headers()) as c,
            c.stream("GET", url) as resp,
        ):
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                yield int(data.get("up", 0) or 0), int(data.get("down", 0) or 0)

    async def test_latencies(self) -> dict[str, int]:
        state = await self.get_state()
        names = [r.name for r in state.routes]
        if not names:
            return {}
        timeout_ms = 5000
        results: dict[str, int] = {}
        async with httpx.AsyncClient(timeout=(timeout_ms / 1000) + 5) as c:
            tasks = [
                c.get(
                    f"{settings.clash_api_url}/proxies/{n}/delay",
                    params={"timeout": timeout_ms, "url": "http://www.gstatic.com/generate_204"},
                    headers=self._headers(),
                )
                for n in names
            ]
            responses = await asyncio.gather(*tasks, return_exceptions=True)
            for name, resp in zip(names, responses, strict=True):
                if isinstance(resp, BaseException):
                    results[name] = -1
                    continue
                try:
                    data = resp.json()
                    results[name] = data.get("delay", -1) if "delay" in data else -1
                except Exception:
                    results[name] = -1
        return results


def _find_clash_config() -> Path | None:
    for candidate in _CLASH_CONFIG_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


def _reorder_in_yaml(group_name: str, new_order: list[str]) -> None:
    config_path = _find_clash_config()
    if config_path is None:
        tried = ", ".join(str(p) for p in _CLASH_CONFIG_CANDIDATES)
        raise FileNotFoundError(f"Clash config not found. Tried: {tried}")

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)

    with config_path.open() as f:
        data = yaml.load(f)

    groups = data.get("proxy-groups", [])
    target = next((g for g in groups if g.get("name") == group_name), None)
    if target is None:
        raise ValueError(f"Proxy group '{group_name}' not found in config")

    existing = list(target.get("proxies", []))
    if set(new_order) != set(existing):
        raise ValueError(f"New order must contain the same proxies as existing: {existing}")

    target["proxies"] = new_order

    # Single-file bind mount: rename(2) fails with EBUSY, so write in place.
    buf = io.StringIO()
    yaml.dump(data, buf)
    with config_path.open("w") as f:
        f.write(buf.getvalue())


async def _wait_for_group_ready(group: str, headers: dict[str, str], timeout_sec: float = 20.0) -> None:
    deadline = time.monotonic() + timeout_sec
    async with httpx.AsyncClient(timeout=2) as c:
        while time.monotonic() < deadline:
            try:
                r = await c.get(f"{settings.clash_api_url}/proxies/{group}", headers=headers)
                if r.status_code == 200 and r.json().get("now"):
                    return
            except Exception:
                pass
            await asyncio.sleep(0.4)


async def _trigger_group_test(group: str, headers: dict[str, str], timeout_ms: int = 5000) -> None:
    """Force Clash to re-probe every route so fallback re-ranks them."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{settings.clash_api_url}/proxies/{group}", headers=headers)
            r.raise_for_status()
            data = r.json()
    except Exception:
        return
    names = [n for n in (data.get("all") or []) if n not in HIDDEN_ROUTES]
    if not names:
        return
    async with httpx.AsyncClient(timeout=(timeout_ms / 1000) + 5) as c:
        tasks = [
            c.get(
                f"{settings.clash_api_url}/proxies/{n}/delay",
                params={"timeout": timeout_ms, "url": "http://www.gstatic.com/generate_204"},
                headers=headers,
            )
            for n in names
        ]
        await asyncio.gather(*tasks, return_exceptions=True)


registry.register("clash", lambda c: ClashController(container_name=c.name))
