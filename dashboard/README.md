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

The Docker container is the single source of truth. Run it; develop against it.

```bash
# 1. Start the dashboard container (from project root)
docker compose up -d --build dashboard

# 2. Frontend hot-reload (optional — Vite proxies /api to :8080)
cd dashboard/frontend
npm install
npm run dev       # Vite dev server on :5173

# Lint
npm run lint      # ESLint
npm run format    # Prettier
cd ../backend && uvx ruff check app/ && uvx ruff format app/
```

Backend changes: rebuild the container (`docker compose up -d --build dashboard`).
Do not run a second backend on the host — there's no `/configs` mount outside the container, so it will 404 on Config requests.

## Build

```bash
# Docker image only
docker build -t proxy-dashboard ./dashboard

# Or via compose
docker compose build dashboard
```
