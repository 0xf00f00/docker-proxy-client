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
4. [`vpn-route.service`](vpn-route.service) applies the default route +
   direct-domain excludes.

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

## Per-flow fairness inside the tunnel (CAKE)

**Requires TUN `stack: gvisor`.** On `stack: system`, a CAKE qdisc on
*either* direction throttles the whole tunnel to that one rate in *both*
directions.

- **Upload** (`CAKE_UL_BANDWIDTH`): root CAKE qdisc on `utun` egress (client→internet).
- **Download** (`CAKE_DL_BANDWIDTH`): root qdiscs are egress-only and decrypted
  download enters `utun` as *ingress* (proxy writes to the TUN), so it's redirected through an `ifb-utun` device carrying its own CAKE qdisc.

Default options are `besteffort nat` (download adds `ingress`). The `nat` flag is load-bearing. Set each rate to ~85–90% of the *measured* WAN rate in that direction (`curl --interface eth0`), so the queue forms on TUN, not at the router.

```bash
sudo nano /etc/default/vpn-route           # set CAKE_UL_BANDWIDTH / CAKE_DL_BANDWIDTH
sudo systemctl start vpn-route.service     # applies live; no clash restart, no outage
tc -s qdisc show dev utun                  # -> qdisc cake ... bandwidth 11Mbit ... nat
tc -s qdisc show dev ifb-utun              # -> qdisc cake ... bandwidth 30Mbit ... ingress
```

To disable: clear the variables and `systemctl start vpn-route.service` again; the script removes the qdisc and IFB itself.

## Install (on the host / Pi)

```bash
sudo install -m 0755 system/vpn-route.sh            /usr/local/bin/vpn-route.sh
sudo install -m 0644 system/vpn-route.service       /etc/systemd/system/vpn-route.service
sudo install -m 0755 system/vpn-route-wait.sh       /usr/local/bin/vpn-route-wait.sh
sudo install -m 0644 system/vpn-route-iface.service /etc/systemd/system/vpn-route-iface.service
sudo install -m 0644 system/99-vpn-route.rules      /etc/udev/rules.d/99-vpn-route.rules

# site config
[ -e /etc/default/vpn-route ] || \
  sudo install -m 0600 system/vpn-route.default /etc/default/vpn-route

sudo systemctl daemon-reload
sudo udevadm control --reload
```

No `systemctl enable` is needed: `vpn-route-iface.service` is pulled in on
demand by the udev `SYSTEMD_WANTS` tag, not at boot.

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
  `KERNEL=="utun"` and `vpn-route.sh` defaults to `utun` (override with
  `VPN_INTERFACE` in `/etc/default/vpn-route`). If
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
