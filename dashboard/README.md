# Proxy Dashboard

Web UI for monitoring, testing, and managing the proxy infrastructure.

## Quick Start

```bash
# Build and run with docker compose (from project root)
docker compose up -d --build dashboard

# Access at http://<HOST_LAN_IP>:8080
```

## Configuration

The host LAN IP is auto-detected from the `MACVLAN_PARENT` interface (already set in `.env`).

Optional settings in `.env`:

```
DASHBOARD_PORT=8080              # Dashboard listen port (default: 8080)
DASHBOARD_BIND=0.0.0.0           # Bind address (0.0.0.0 = LAN-accessible, 127.0.0.1 = local only)
CLASH_API_SECRET=                # Optional: Clash external-controller secret
```

## Adding a Service to the Dashboard

Add `dashboard.*` labels to any service in `docker-compose.yml`:

```yaml
my-proxy:
  image: example/proxy:latest
  labels:
    - dashboard.enable=true          # Required: opt in
    - dashboard.category=proxy       # proxy | dns | infra
    - dashboard.name=My Proxy        # Display name in UI
    - dashboard.protocol=socks5      # socks5 | http | mixed | tls | dns | tunnel
    - dashboard.port=1080            # The port a user connects to on the LAN (see below)
    - dashboard.testable=true        # Run connectivity test (default: true)
    - dashboard.widget=system-proxy  # Optional: custom widget (system-proxy | dnstt-dns)
    - dashboard.controller=clash     # Optional: registered SystemProxyController name (e.g. clash, xray)
    - dashboard.config=/path/in/cfg  # Optional: path under /configs for editing
```

### `dashboard.port`

This is the **port a user types into their proxy app**, not the container-internal listening port. The dashboard derives the host side automatically from Docker's live network config; the label only needs to say which port on the LAN to advertise.

| Networking | `dashboard.port` value | Resolves to |
|---|---|---|
| `network_mode: host` | The port the service listens on | `HOST_LAN_IP:port` |
| `ports: ["5080:1080"]` (any network) | The **host-side** port (`5080`) | `HOST_LAN_IP:5080` |
| Attached to `direct_internet` macvlan | The port the service listens on | `<container-macvlan-ip>:port` |
| Bridge with no published port | n/a — service is internal-only and won't appear with a LAN address | — |

### Config file editing

To enable config editing for a service:

1. Add a volume mount in the `dashboard` service mapping the host file to `/configs/<name>/<file>`:
   ```yaml
   dashboard:
     volumes:
       - ./my-proxy/config.json:/configs/my-proxy/config.json
   ```
2. Add the label pointing to the path inside `/configs`:
   ```yaml
   my-proxy:
     labels:
       - dashboard.config=/my-proxy/config.json
   ```

## Removing a Service

Set `dashboard.enable=false` or remove all `dashboard.*` labels.

## Development

Hot-reload dev with **no image rebuilds**, served on the **same port as production (http://localhost:8080)**:

```bash
# 1. Backend — runs in-container (host networking) with uvicorn --reload on a
#    bind-mounted source, so edits hot-reload in place. Brings up the stack too.
#    Use --build the first time, or when backend deps change.
docker compose up -d --build dashboard

# 2. Frontend — native Vite HMR on :8080, proxies /api to the backend on :8081
cd dashboard/frontend && npm install && npm run dev
```

Open **http://localhost:8080**. Edit a `.py` → the container's uvicorn reloads; edit a `.tsx` → instant Vite HMR.

**Why the backend runs in-container (not host-native):** Docker Desktop runs containers inside a VM, and macOS has no route to the proxy bridge/macvlan subnets (`10.20.0.x`, `172.30.0.x`). A host-native backend could only reach services that publish a port, so connectivity probes for bridge-only services (byedpi, frp, mdns, …) would fail. Running the backend on host networking — exactly like production — lets it reach every container's bridge IP. The dev bind-mount + `--reload` wiring lives in `docker-compose.override.yml` (local, uncommitted).

```bash
# Lint / format
cd dashboard/frontend && npm run lint && npm run format
cd dashboard/backend  && uvx ruff check app/ && uvx ruff format app/

# Rebuild the production image (only when testing the artifact)
docker compose up -d --build dashboard
```

## Build

```bash
# Docker image only
docker build -t proxy-dashboard ./dashboard

# Or via compose
docker compose build dashboard
```
