# cf-edge-manager

One Go service that unifies what used to be three: **discovery** (cfst scans &
ranks Cloudflare edges), **runtime selection** (the in-process picker that
rotates the in-use edge), and **real-path survival probing** (embedded
sni-spoofing-go + a bundled xray binary). It exposes a small control API the
dashboard talks to, plus `GET /manager/status`.

## Default behavior: nothing runs on its own

> **Out of the box, this container does no automated work.** It boots, serves its
> control API, and then waits. No scan runs until the dashboard (or you) triggers
> one; no edge gets probed, rotated, or quarantined; `sni-spoofing-fallback` is
> never rewritten or restarted.

Every autonomous behavior is **opt-in**.

## The two automated behaviors

| Behavior | Switch (`.env` / internal) | Default | What it does when ON | Blast radius |
|----------|---------------------------|---------|----------------------|--------------|
| **Scheduled discovery scan** | `CF_SCAN_CRON` / `SCAN_CRON` | *empty (off)* | Runs a full cfst scan on a cron schedule and rewrites the edge pool. | Saturates probe traffic on the uplink for its duration; does **not** touch live routing. The dashboard can always scan on demand regardless. |
| **Runtime edge selection** | `CF_SELECT_ENABLE` / `SELECT_ENABLE` | `false` | Health-checks the in-use edge, probes candidates, rotates off degraded edges, quarantines bad ones, and **rewrites + restarts `sni-spoofing-fallback`**. | The one that changes live routing; restarts the fallback edge container when it repoints (rate-limited by `RESTART_MIN_GAP_S`). |

`SELECT_ENABLE` gates *all* runtime autonomy: the select loop, the real-path
probe (`PROBE_ENABLE`), quarantine, config rewrites, and container restarts. With
it off, none of those code paths run, no matter what the other knobs say.

### Enabling them

In `.env` (consumed by `docker-compose.yml`):

```bash
# Enable scheduled scans, Mondays 05:00 in TZ:
CF_SCAN_CRON=0 0 5 * * 1
TZ=America/New_York

# Enable automatic edge rotation (requires the wire params below to be set):
CF_SELECT_ENABLE=true
CF_PROBE_UUID=...          # vless id of the main xray "proxy" outbound
CF_PROBE_PATH=...          # xHTTP path of that outbound
CF_PROBE_ORIGIN_HOST=...   # the fronted origin host
```

`CF_SCAN_CRON` is a 6-field cron with a **leading seconds column**
(robfig/cron), evaluated in the container `TZ`. Empty disables it. If you set
`CF_SELECT_ENABLE=true` without `CF_PROBE_UUID`/`CF_PROBE_ORIGIN_HOST`, the
manager **fails loudly at startup** rather than silently mis-rotating.

## Config reference

Read from environment once at startup (no hot reload; restart to apply). Defaults
live in [`internal/config/config.go`](internal/config/config.go). Names below are
the internal `<NAME>`; prefix with `CF_` for the `.env` form.

### Tier 1 — automation switches (the only thing most deployments set)

| `.env` | Default | Purpose |
|--------|---------|---------|
| `CF_SCAN_CRON` | *empty* | Cron for scheduled scans. Empty = off. |
| `TZ` | `UTC` | Timezone for `CF_SCAN_CRON`. |
| `CF_SELECT_ENABLE` | `false` | Master switch for all runtime autonomy. |

### Tier 2 — required when `CF_SELECT_ENABLE=true`

Must match the live main xray `proxy` outbound + sni-spoofing config, or the
real-path probe reads every edge as a false failure.

| `.env` | Default | Purpose |
|--------|---------|---------|
| `CF_PROBE_UUID` | *(required)* | VLESS id of the main xray outbound. |
| `CF_PROBE_PATH` | `/` | xHTTP path of that outbound. |
| `CF_PROBE_ORIGIN_HOST` | *(required)* | Fronted origin host. |
| `FALLBACK_HOST` | *(required)* | Fallback edge-path fronted host (→ internal `FALLBACK_FRONT_HOST`). |
| `CF_PROBE_FAKE_SNI` | `hcaptcha.com` | sni-spoofing decoy SNI (match production). |
| `CF_PROBE_FAKE_UTLS` | `firefox` | sni-spoofing decoy-hello uTLS fingerprint (the hello DPI inspects). |
| `CF_PROBE_REAL_UTLS` | `chrome` | xray real-hello uTLS fingerprint (the hello that reaches CF/origin). |
| `CF_PROBE_FRAGMENT` | `false` | Real-ClientHello fragmentation. Keep `false` — A/B showed fragment-on is *worse* here. |

### Tier 3 — tuning knobs (defaults are fine; override only with a measured reason)

Not wired into `docker-compose.yml`; the container reads them if set in the env.

**Discovery / cfst:** `SCAN_THREADS` (5), `SCAN_CONNECTS` (10), `EDGE_PORT`
(443), `SCAN_LOSS_MAX` (0.10), `SCAN_LAT_MAX_MS` (1000), `POOL_SIZE` (10),
`SCAN_TIMEOUT_S` (21600), `SCAN_PREFLIGHT` (true — skip a scan when no upstream is
reachable at all), `RANGES_FILE` (`/cf-ranges.txt`), `OUT_DIR` (`/out`),
`CFST_BIN` (`cfst`).

**Interactive single-IP test:** `TEST_CONNECTS` (30), `TEST_CONCURRENCY` (3),
`TEST_COOLDOWN_S` (30), `TEST_QUEUE_MAX` (64), `TEST_TIMEOUT_S` (180).

**Fake-SNI pre-rank gate** (`TLS_GATE_ENABLE`, false — measured *not* to match
production, prefer the real-path probe): `TLS_SNI`, `TLS_HOLD_S`,
`TLS_TIMEOUT_S`, `TLS_GAP_S`, `TLS_BURST` (12), `TLS_FAIL_MAX` (0.10).

**Selection loop cadence/thresholds** (active only when `SELECT_ENABLE=true`):
`SELECT_INTERVAL_S` (600), `SELECT_MAX_BACKOFF_S` (21600), `SELECT_SLEEP_CAP_S`
(300), `KEEP_LOSS_MAX` (0.20), `PICK_LOSS_MAX` (0.10), `SELECT_MAX_CANDIDATES`
(3), `PROBE_MAX_CANDIDATES` (2), `RESTART_MIN_GAP_S` (300), `QUARANTINE_TTL_S`
(3600), `LOSS_CONNECTS` (10), `LOSS_TIMEOUT_S` (2).

**Real-path survival probe** (`PROBE_ENABLE`, true — gated by `SELECT_ENABLE`;
false reverts the picker to loss-only): `PROBE_URL`
(`http://www.gstatic.com/generate_204`), `XRAY_BIN`, `PROBE_COUNT` (4),
`PROBE_CONCURRENCY` (2), `PROBE_MIN_GAP_S` (10), `PROBE_MAX_GAP_S` (300),
`PROBE_CACHE_TTL_S` (1800).

**Apply targets / wiring:** `API_ADDR` (`:8088`), `COREDNS_HOSTS`,
`SNISPOOF_CONF`, `SNISPOOF_CONTAINER` (`sni-spoofing-fallback`), `DOCKER_SOCK`,
`SELECT_STATE` (`/state/picker.json`).

## Disabling everything (return to inert state)

```bash
CF_SCAN_CRON=          # no scheduled scans
CF_SELECT_ENABLE=false # no rotation, probing, quarantine, or container restarts
```

The dashboard's on-demand scan/test endpoints keep working — those are explicit
user actions, not automation.
