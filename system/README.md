# VPN routing units

Host-side systemd glue that keeps the system default route pointed through
Clash's TUN device (`utun`).

Clash runs `network_mode: host`, so the TUN it creates lives in the **host**
network namespace and the host can route through it. Clash is configured with
`auto-route: false` (its built-in auto-route is unreliable) — so route setup is
externalized to [`vpn-route.sh`](vpn-route.sh), which also adds DNS-resolved
`/32` "direct" routes for a handful of domains (NTP, Argo tunnel, …) via the
**eth0** gateway so they bypass the tunnel.

## The problem this solves

Every time Clash (re)starts — manual `docker restart clash`, `docker compose
restart clash`, a dashboard config save/reorder, or autoheal — the old `utun`
is destroyed and a fresh one created. The kernel drops the default route bound
to the old device, so **all traffic has no default route until something
re-applies it.** With only the 5-minute timer (below) that's up to a 5-minute
outage.

## How it works

The fix is event-driven, triggered off the TUN device appearing:

1. Clash starts → kernel creates `utun` → udev `add` event.
2. [`99-vpn-route.rules`](99-vpn-route.rules) matches it and, via
   `ENV{SYSTEMD_WANTS}`, pulls in `vpn-route-iface.service`.
   (We hand off to systemd rather than acting in the udev rule: udev kills
   long-running `RUN+=` commands.)
3. [`vpn-route-iface.service`](vpn-route-iface.service) runs
   [`vpn-route-wait.sh`](vpn-route-wait.sh), which polls until `utun` is
   carrier-ready, then `exec systemctl start vpn-route.service`.
4. [`vpn-route.service`](vpn-route.service) (unchanged) applies the default
   route + direct-domain excludes. Keeping the arguments in this one unit means
   there's a single source of truth.

Because **every** restart path ends in a fresh `utun` `add` event, this single
mechanism covers manual restarts, `compose restart`, dashboard-driven restarts,
and autoheal alike — no dashboard code changes needed. Outage window drops from
up to 5 min to however long Clash takes to bring the TUN up (~1–3s).

### Why poll for `LOWER_UP` instead of using the device on `add`

udev's `add` fires when the kernel *registers* the netdev — **before** Clash
brings it up and attaches its fd. Routing through it then fails. There is no
udev event for "carrier came up," so we trigger on `add` and gate on the
interface flags:

- `UP` — administratively up
- `LOWER_UP` — carrier present, i.e. Clash holds the tun fd and is moving
  packets. This is the real "assigned, up and running" moment.

We deliberately do **not** use `systemd-networkd-wait-online`: it keys off
`operstate`, which is `UNKNOWN` for a TUN, so it would hang or misfire. The
flag-based poll is more correct for a TUN.

### Why not systemd-networkd's declarative `[Route]`

The canonical systemd way would be a `.network` file that owns `utun` and
declares the route — edge-triggered and idempotent with no poll. It doesn't fit
here: the direct-domain excludes are **dynamic, DNS-resolved** routes via the
eth0 gateway, which networkd's declarative stanzas can't express. Splitting the
default route (networkd) from the excludes (script) would be worse than one
imperative script triggered on device appearance.

## Install (on the host / Pi)

```bash
sudo install -m 0755 system/vpn-route-wait.sh       /usr/local/bin/vpn-route-wait.sh
sudo install -m 0644 system/vpn-route-iface.service /etc/systemd/system/vpn-route-iface.service
sudo install -m 0644 system/99-vpn-route.rules      /etc/udev/rules.d/99-vpn-route.rules

sudo systemctl daemon-reload
sudo udevadm control --reload
```

No `systemctl enable` is needed: `vpn-route-iface.service` is pulled in on
demand by the udev `SYSTEMD_WANTS` tag, not at boot.

`vpn-route.sh`, `vpn-route.service`, and `vpn-route.timer` are the pre-existing
units and are installed/enabled separately (the timer stays on as a backstop —
see below).

## Verify

```bash
docker restart clash
# watch the gate fire and the route re-apply:
journalctl -u vpn-route-iface.service -u vpn-route.service -f
# confirm the default route is back:
ip route show default
```

## Before installing — confirm assumptions

- **Device name is literally `utun`:** `ip link show`. The udev rule matches
  `KERNEL=="utun"` and `vpn-route.service` hardcodes `--vpn-interface utun`. If
  Clash ever names it with a suffix (`utun0`, …), switch the rule to
  `KERNEL=="utun*"`, rename the unit to `vpn-route-iface@.service`, set
  `ENV{SYSTEMD_WANTS}="vpn-route-iface@$name.service"`, and change `ExecStart`
  to `… /usr/local/bin/vpn-route-wait.sh %i 30` so the real name flows through.

- **Nothing else manages `utun`:** `networkctl` and `nmcli device status`. If
  systemd-networkd or NetworkManager thinks it owns the interface, two parties
  can race on its routes. `utun` should be unmanaged.

## Backstop

Keep [`vpn-route.timer`](vpn-route.timer) enabled as a safety net in case udev
ever misses an event. Since the udev trigger is now the primary path, the timer
interval can be relaxed (e.g. 15 min).
