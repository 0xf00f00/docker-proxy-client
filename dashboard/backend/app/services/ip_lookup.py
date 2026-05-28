"""Public-IP / country lookup, optionally tunneled through a given proxy.

Used by the connectivity tests to enrich a successful probe with the egress
IP+country (so the dashboard can show "via this proxy you exit as 🇺🇸 X.X.X.X").
Same machinery serves the system-proxy widget — pass the active proxy URL.

Several free APIs are tried in order. The list is ordered by: rich JSON
first, then minimal-and-permissive, then a rate-limited last resort. Each
provider gets a tight per-attempt timeout so a slow one can't hold up the
whole probe. Results are cached briefly per (proxy_url) so rapid re-renders
don't re-hammer the APIs.
"""

import asyncio
import time

import httpx

from app.models.schemas import IpInfo

PER_ATTEMPT_TIMEOUT = 4.0
CACHE_TTL_S = 60.0

_cache: dict[str | None, tuple[float, IpInfo | None]] = {}
_cache_lock = asyncio.Lock()


def _flag_emoji(country_code: str | None) -> str | None:
    """Convert ISO-3166 alpha-2 to a regional-indicator flag emoji."""
    if not country_code or len(country_code) != 2 or not country_code.isalpha():
        return None
    cc = country_code.upper()
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in cc)


async def _try_cloudflare_trace(client: httpx.AsyncClient) -> IpInfo | None:
    """Primary: Cloudflare's cdn-cgi/trace — plain-text k=v, no auth, ~one
    HTTPS round-trip. Returns ip+loc (ISO country); no ASN/ISP/city."""
    r = await client.get("https://www.cloudflare.com/cdn-cgi/trace", timeout=PER_ATTEMPT_TIMEOUT)
    r.raise_for_status()
    fields: dict[str, str] = {}
    for line in r.text.splitlines():
        k, _, v = line.partition("=")
        if k:
            fields[k.strip()] = v.strip()
    ip = fields.get("ip")
    if not ip:
        return None
    cc = fields.get("loc") or None
    return IpInfo(ip=ip, country_code=cc, flag_emoji=_flag_emoji(cc))


async def _try_ipwho_is(client: httpx.AsyncClient) -> IpInfo | None:
    """Fallback: ipwho.is — rich JSON, no key, tolerant of proxy IPs. Adds
    ASN/ISP/city when the primary couldn't reach Cloudflare."""
    r = await client.get("https://ipwho.is/", timeout=PER_ATTEMPT_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if not data.get("success", False):
        return None
    ip = data.get("ip")
    if not ip:
        return None
    cc = data.get("country_code")
    conn = data.get("connection") or {}
    flag = (data.get("flag") or {}).get("emoji") or _flag_emoji(cc)
    return IpInfo(
        ip=ip,
        country_code=cc,
        country_name=data.get("country"),
        flag_emoji=flag,
        city=data.get("city"),
        asn=str(conn.get("asn")) if conn.get("asn") is not None else None,
        isp=conn.get("isp") or conn.get("org"),
    )


async def _try_country_is(client: httpx.AsyncClient) -> IpInfo | None:
    """Fallback 1: api.country.is — minimal {ip, country}, near-zero block risk."""
    r = await client.get("https://api.country.is/", timeout=PER_ATTEMPT_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    ip = data.get("ip")
    if not ip:
        return None
    cc = data.get("country")
    return IpInfo(ip=ip, country_code=cc, flag_emoji=_flag_emoji(cc))


async def _try_ifconfig_co(client: httpx.AsyncClient) -> IpInfo | None:
    """Fallback 2: ifconfig.co/json — rich, but self-limits to 1 req/min."""
    r = await client.get(
        "https://ifconfig.co/json",
        timeout=PER_ATTEMPT_TIMEOUT,
        headers={"Accept": "application/json", "User-Agent": "curl/8"},
    )
    r.raise_for_status()
    data = r.json()
    ip = data.get("ip")
    if not ip:
        return None
    cc = data.get("country_iso")
    return IpInfo(
        ip=ip,
        country_code=cc,
        country_name=data.get("country"),
        flag_emoji=_flag_emoji(cc),
        city=data.get("city"),
        asn=str(data.get("asn")) if data.get("asn") is not None else None,
        isp=data.get("asn_org"),
    )


_PROVIDERS = (_try_cloudflare_trace, _try_ipwho_is, _try_country_is, _try_ifconfig_co)


async def lookup(proxy_url: str | None) -> IpInfo | None:
    """Return the public IP + country, optionally as seen through `proxy_url`.

    Passing `None` queries from the dashboard backend's own egress (used for
    e.g. the system-proxy widget when the dashboard host shares egress with
    the proxy). Returns None if every provider fails.
    """
    now = time.monotonic()
    async with _cache_lock:
        cached = _cache.get(proxy_url)
        if cached and now - cached[0] < CACHE_TTL_S:
            return cached[1]

    result: IpInfo | None = None
    try:
        async with httpx.AsyncClient(proxy=proxy_url, follow_redirects=True) as client:
            for provider in _PROVIDERS:
                try:
                    result = await provider(client)
                    if result is not None:
                        break
                except Exception:
                    continue
    except Exception:
        result = None

    async with _cache_lock:
        _cache[proxy_url] = (time.monotonic(), result)
    return result
