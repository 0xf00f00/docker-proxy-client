#!/bin/bash
# Script to check and add default route for a VPN interface
# and add direct routes for specified domains via a specified default interface's gateway.
# Site config comes from the environment (/etc/default/vpn-route via the systemd
# unit); CLI flags override env.

# Function to display usage information
usage() {
    echo "Usage: $0 [--vpn-interface <vpn_interface>] [--default-interface <default_interface>] [--direct-domains <domain1,domain2,...>] [--dhcp]"
    echo
    echo "Options (each falls back to the same-named env var, then to a default):"
    echo "  --vpn-interface       Name of the VPN interface (env VPN_INTERFACE, default utun)"
    echo "  --default-interface   Interface to fetch the direct gateway from (env DEFAULT_INTERFACE, default eth0)"
    echo "  --direct-domains      Comma-separated domains to route direct (env DIRECT_DOMAINS)"
    echo "  --dhcp                Use DHCP to obtain IP and gateway for the VPN interface if not set"
    echo "  -h, --help            Display this help message"
    echo
    echo "CAKE shaping (env only): CAKE_UL_BANDWIDTH/CAKE_UL_OPTIONS (upload),"
    echo "CAKE_DL_BANDWIDTH/CAKE_DL_OPTIONS (download via IFB)."
    exit 1
}

# Function to parse command-line arguments (flags override env, env overrides defaults)
parse_arguments() {
    VPN_INTERFACE="${VPN_INTERFACE:-utun}"
    DEFAULT_INTERFACE="${DEFAULT_INTERFACE:-eth0}"
    local domains_csv="${DIRECT_DOMAINS:-}"
    USE_DHCP=false

    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --vpn-interface)
                VPN_INTERFACE="$2"
                shift 2
                ;;
            --default-interface)
                DEFAULT_INTERFACE="$2"
                shift 2
                ;;
            --direct-domains)
                domains_csv="$2"
                shift 2
                ;;
            --dhcp)
                USE_DHCP=true
                shift
                ;;
            -h|--help)
                usage
                ;;
            *)
                echo "Unknown option: $1"
                usage
                ;;
        esac
    done

    DIRECT_DOMAINS=()
    if [ -n "$domains_csv" ]; then
        IFS=',' read -ra DIRECT_DOMAINS <<< "$domains_csv"
    fi

    if [ -z "$VPN_INTERFACE" ] || [ -z "$DEFAULT_INTERFACE" ]; then
        echo "Error: --vpn-interface and --default-interface are required."
        usage
    fi
}

# Function to check if script is run as root
check_root() {
    if [ "$EUID" -ne 0 ]; then
        echo "This script must be run as root. Please run it using 'sudo' or as the root user."
        exit 1
    fi
}

# Function to check if an interface is up
is_interface_up() {
    local interface=$1
    ip link show "$interface" up > /dev/null 2>&1
}

# Function to get the default gateway for a given interface
get_gateway() {
    local interface=$1
    ip route show dev "$interface" | awk '/default/ {print $3}'
}

# Function to resolve the IP addresses of a domain
resolve_domain_ips() {
    local domain=$1
    host "$domain" | awk '/has address/ { print $4 }'
}

# Function to add a direct route for a given IP via a gateway
add_direct_route() {
    local ip_address=$1
    local gateway=$2
    if ! ip route | grep -qE "^$ip_address"; then
        ip route add "$ip_address" via "$gateway"
        echo "Route added for $ip_address via gateway $gateway."
    # else
    #     echo "Route for $ip_address already exists."
    fi
}

# Function to add default route via VPN interface
add_vpn_route() {
    local gateway

    route_spec="dev $VPN_INTERFACE"

    # Try to get the gateway for the VPN interface
    gateway=$(get_gateway "$VPN_INTERFACE")

    if [ -z "$gateway" ] && [ "$USE_DHCP" = true ]; then
        echo "No gateway found for $VPN_INTERFACE. Requesting IP via DHCP."
        dhclient -1 -v "$VPN_INTERFACE"

        # After DHCP, try to get the gateway again
        gateway=$(get_gateway "$VPN_INTERFACE")

        if [ -n "$gateway" ]; then
        route_spec="via $gateway"
        fi
    fi

    # Add the default route if it doesn't already exist
    if ! ip route | grep -q "default.*$route_spec"; then
        ip route add default $route_spec
        echo "Default route added: default $route_spec"
    # else
    #     echo "Default route already exists: default $route_spec"
    fi
}

# tc silently accepts unit-less rates as bytes/sec ("12" = 8bit/s = outage),
# so require an explicit K/M/G unit.
is_valid_rate() {
    [[ "$1" =~ ^[0-9]+(\.[0-9]+)?[KkMmGg](bit|bps)$ ]]
}

have_tc() {
    command -v tc > /dev/null 2>&1 && return 0
    echo "tc not found (install iproute2); skipping CAKE qdisc." >&2
    return 1
}

# Function to apply a CAKE qdisc on the VPN interface egress (upload direction).
# "nat" is required for per-client fairness: POSTROUTING masquerades onto the
# tunnel, so without the conntrack lookup all clients hash to one host.
apply_cake_qdisc() {
    if [ -z "${CAKE_UL_BANDWIDTH:-}" ]; then
        if have_tc && tc qdisc show dev "$VPN_INTERFACE" 2>/dev/null | grep -q '^qdisc cake'; then
            tc qdisc del dev "$VPN_INTERFACE" root
            echo "CAKE upload shaping removed (CAKE_UL_BANDWIDTH unset)."
        fi
        return 0
    fi
    have_tc || return 0
    if ! is_valid_rate "$CAKE_UL_BANDWIDTH"; then
        echo "CAKE_UL_BANDWIDTH '$CAKE_UL_BANDWIDTH' rejected: unit suffix required (e.g. 11Mbit); not applying." >&2
        return 0
    fi
    if tc qdisc replace dev "$VPN_INTERFACE" root cake bandwidth "$CAKE_UL_BANDWIDTH" ${CAKE_UL_OPTIONS:-besteffort nat}; then
        echo "CAKE upload on $VPN_INTERFACE: bandwidth $CAKE_UL_BANDWIDTH ${CAKE_UL_OPTIONS:-besteffort nat}"
    else
        echo "Failed to apply CAKE qdisc on $VPN_INTERFACE." >&2
    fi
}

# Function to shape the download direction: decrypted tunnel traffic arrives as
# VPN-interface ingress, which a root qdisc can't touch, so redirect it through
# an IFB device and put CAKE there. "nat" again: at ingress the dst is still the
# masqueraded tunnel address (PREROUTING hasn't run yet).
apply_cake_ingress() {
    local ifb="ifb-$VPN_INTERFACE"
    if [ -z "${CAKE_DL_BANDWIDTH:-}" ]; then
        if ip link show "$ifb" > /dev/null 2>&1; then
            tc qdisc del dev "$VPN_INTERFACE" ingress 2>/dev/null
            ip link del "$ifb"
            echo "CAKE download shaping removed (CAKE_DL_BANDWIDTH unset)."
        fi
        return 0
    fi
    have_tc || return 0
    if ! is_valid_rate "$CAKE_DL_BANDWIDTH"; then
        echo "CAKE_DL_BANDWIDTH '$CAKE_DL_BANDWIDTH' rejected: unit suffix required (e.g. 28Mbit); not applying." >&2
        return 0
    fi
    modprobe ifb numifbs=0 2>/dev/null
    ip link show "$ifb" > /dev/null 2>&1 || ip link add "$ifb" type ifb
    ip link set "$ifb" mtu "$(cat /sys/class/net/"$VPN_INTERFACE"/mtu)" up
    tc qdisc replace dev "$VPN_INTERFACE" handle ffff: ingress
    tc filter show dev "$VPN_INTERFACE" parent ffff: 2>/dev/null | grep -q "$ifb" \
        || tc filter add dev "$VPN_INTERFACE" parent ffff: protocol all prio 1 matchall \
               action mirred egress redirect dev "$ifb"
    if tc qdisc replace dev "$ifb" root cake bandwidth "$CAKE_DL_BANDWIDTH" ${CAKE_DL_OPTIONS:-besteffort nat ingress}; then
        echo "CAKE download on $ifb: bandwidth $CAKE_DL_BANDWIDTH ${CAKE_DL_OPTIONS:-besteffort nat ingress}"
    else
        echo "Failed to apply CAKE qdisc on $ifb." >&2
    fi
}

# Function to add direct routes for all domains in DIRECT_DOMAINS
add_direct_routes() {
    local gateway=$1

    for DOMAIN_NAME in "${DIRECT_DOMAINS[@]}"; do
        # Resolve the IP addresses of DOMAIN_NAME
        IP_ADDRESSES=$(resolve_domain_ips "$DOMAIN_NAME")
        if [ -z "$IP_ADDRESSES" ]; then
            echo "Could not resolve IP addresses for domain $DOMAIN_NAME. Skipping."
            continue
        fi

        # Add direct route for each IP_ADDRESS via GATEWAY
        for IP_ADDRESS in $IP_ADDRESSES; do
            add_direct_route "$IP_ADDRESS" "$gateway"
        done
    done
}

# Main function
main() {
    # Check if script is run as root
    check_root

    # Parse command-line arguments
    parse_arguments "$@"

    # Check if VPN interface is up; exit early if not
    if ! is_interface_up "$VPN_INTERFACE"; then
        echo "$VPN_INTERFACE interface is not up. Exiting with failure."
        exit 1
    fi

    # Add default route via VPN interface
    add_vpn_route

    # Per-flow fairness inside the tunnel; each is a no-op unless its
    # CAKE*_BANDWIDTH is set, and cleans up after itself when unset.
    apply_cake_qdisc
    apply_cake_ingress

    # Get the default gateway for DEFAULT_INTERFACE
    GATEWAY=$(get_gateway "$DEFAULT_INTERFACE")
    if [ -z "$GATEWAY" ]; then
        echo "Could not find default gateway for interface $DEFAULT_INTERFACE. Exiting."
        exit 1
    fi

    # Add direct routes for specified domains if any
    if [ ${#DIRECT_DOMAINS[@]} -gt 0 ]; then
        add_direct_routes "$GATEWAY"
    fi

    exit 0  # Success
}

# Run the main function with all script arguments
main "$@"
