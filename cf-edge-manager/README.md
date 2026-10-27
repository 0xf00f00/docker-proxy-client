# cf-edge-manager

One Go service that unifies what used to be three: **discovery** (cfst scans &
ranks Cloudflare edges), **runtime selection** (rotates the in-use edge of each
sni-spoofing instance), and **real-path probing** (embedded sni-spoofing-go + a
bundled xray binary). It exposes a small control API the dashboard talks to, plus
`GET /manager/status`.

## Nothing runs on its own

Out of the box this container only serves its control API. Every autonomous
behavior is opt-in:

| Behavior | Switch | What it does when on |
|----------|--------|----------------------|
| **Scheduled scan** | `CF_SCAN_ENABLE` | Runs a cfst scan on `CF_SCAN_CRON` (default daily) and refreshes the edge pool. Never touches live routing. The dashboard can always scan on demand. |
| **Edge selection** | `CF_SELECT_ENABLE` | Health-checks each instance's edge, rotates off bad ones, and **rewrites + restarts** the sni-spoofing containers. This is the one that changes live routing. |

## Configuration

Everything an operator is expected to set, in `.env`:

| `.env` | Default | Purpose |
|--------|---------|---------|
| `CF_SCAN_ENABLE` | `false` | Scheduled scans. |
| `CF_SCAN_CRON` | daily | 6-field cron with a **leading seconds column** (`0 0 5 * * 1` = Mondays 05:00), evaluated in `TZ`. |
| `TZ` | `UTC` | Timezone for the cron and the dashboard's day boundaries. |
| `CF_SELECT_ENABLE` | `false` | Edge selection. Needs the probe values below. |
| `CF_SNISPOOF_INSTANCES` | `sni-spoofing,sni-spoofing-fallback` | Containers to manage, in priority order. Each name is the container and the directory holding its `config.ini`, mounted at `/instances/<name>` in `docker-compose.yml`. Add a name and a mount to manage another. |
| `FALLBACK_HOST` | required | Fronted host of the fallback edge path. |
| `CF_PROBE_UUID` | required with selection | VLESS id of the main xray outbound. |
| `CF_PROBE_PATH` | `/` | xHTTP path of that outbound. |
| `CF_PROBE_ORIGIN_HOST` | required with selection | Fronted origin host. |

The probe values must match the live xray `proxy` outbound, or every edge reads as
a failure; the manager refuses to start with selection on and no `CF_PROBE_UUID`
or `CF_PROBE_ORIGIN_HOST`.

Everything else (thresholds, cadences, timeouts, pool size) is deliberately fixed:
see `Defaults` in [`internal/config/config.go`](internal/config/config.go). To
return to the inert state, unset both switches.

## How an edge is judged

Three signals, each covering what the one before cannot see:

1. **First-attempt TCP loss.** Connects with a timeout under the kernel's 1 s SYN
   retransmit. With a longer timeout a retry rescues a lost SYN, which squares the
   measured loss and hides most of an impaired path.
2. **Real-path probe** (embedded sni-spoofing + xray). Graded: a running edge is
   kept while at most half the probes fail, a candidate must pass every one.
3. **Passive handshake signal.** sni-spoofing logs every proxied connection and
   every one the edge never ACKed. The manager reads that share from the container
   logs, so it measures real traffic under real load with no probe traffic. An edge
   is bad when the lower 95% confidence bound of its failure share exceeds 8%.
   Lines from before the container's last start or the network's last recovery are
   ignored, and nothing is concluded while a restart is staged or when every busy
   instance fails at once (that points at the decoy or the network, not one edge).

The probes only see a brief, low-load sample, so an edge that fails a fifth of real
connections some of the time can pass them. Signal 3 is what catches it.

## Not crying wolf

The network this runs on can be down for hours to months, so acting on a bad
reading is the exception:

- **Network gate.** Each cycle the manager measures control edges unrelated to the
  instance being judged: the instances' own plus a random sample of the pool. If
  most are unreachable (*blackout*) or lossy (*degraded*), no edge reading means
  anything and it takes **no action**: no rotation, quarantine, restart, decoy
  change, real-path probe or scheduled scan. One throttled edge among healthy
  controls is not an outage and is judged on its own. A hold costs a few dozen SYNs
  every five minutes and logs only when it changes.
- **Hysteresis.** A hold starts at the first bad reading and ends only after three
  consecutive good ones, so a flapping network never looks recovered. Evidence
  gathered before the recovery is discarded.
- **Confirmation.** A bad reading must repeat on three consecutive checks (about
  twenty minutes); one good check starts the count over.
- **Circuit breaker.** An instance changes edge at most four times a day.
- **Strict adoption.** A replacement must pass every probe with near-zero loss.
- **Scans.** A scan that finds under a quarter of the pool size keeps the previous
  pool, since it ran in a bad window.

`GET /manager/status` shows the network state and each instance's edge, decoy and
current handshake failure share.

## Decoy SNI

`fake-sni` in each sni-spoofing config is kept working automatically: when it stops
working on the real path the manager switches to a name from
`internal/decoy/seeds.txt` that does (never one another instance uses), rewrites the
config and restarts the container. `POST /manager/decoys/screen` shows which names
pass plain TLS now.
