#!/bin/bash
# Triggered (via udev -> vpn-route-iface.service) when the clash TUN device
# appears. Waits until the interface is actually usable, then delegates to the
# existing vpn-route.service so the default route + direct-domain excludes are
# applied with a single source of truth for the arguments.
#
# Why a wait loop: udev's "add" event fires when the kernel registers the
# netdev, which is BEFORE clash brings it up and attaches its fd. Routing
# through it before then fails.
#
# Why the LOWER_UP gate: TUN devices report operstate UNKNOWN even when fully
# functional, so `state up` is meaningless here. The trustworthy signals are the
# interface flags:
#   UP        -> administratively up
#   LOWER_UP  -> carrier present, i.e. clash holds the tun fd and is moving
#                packets. This is the real "assigned, up and running" moment.
#
# Why not `systemd-networkd-wait-online -i utun`: the stock readiness tool keys
# off operstate, which is UNKNOWN for a TUN -- so it would hang or misfire here.
# Gating on the interface flags is more correct for a TUN, hence this small
# hand-rolled poll instead of the standard tool.
set -uo pipefail

IFACE="${1:-utun}"
TIMEOUT="${2:-30}"   # seconds to wait for the interface to come up

# `ip -o link show <iface> up` prints a line only when the admin UP flag is set;
# grepping LOWER_UP then confirms carrier/attachment.
for _ in $(seq 1 $((TIMEOUT * 2))); do
    if ip -o link show "$IFACE" up 2>/dev/null | grep -q 'LOWER_UP'; then
        exec systemctl start vpn-route.service
    fi
    sleep 0.5
done

echo "vpn-route-wait: $IFACE did not become carrier-ready within ${TIMEOUT}s" >&2
exit 1
