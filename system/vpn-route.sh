#!/bin/bash
# Script to check and add default route for a VPN interface
# and add direct routes for specified domains via a specified default interface's gateway.

# Function to display usage information
usage() {
    echo "Usage: $0 --vpn-interface <vpn_interface> --default-interface <default_interface> [--direct-domains <domain1,domain2,...>] [--dhcp]"
    echo
    echo "Options:"
    echo "  --vpn-interface       Name of the VPN interface (required)"
    echo "  --default-interface   Name of the default network interface to fetch the gateway from (required)"
    echo "  --direct-domains      Comma-separated list of domains for which to add direct routes (optional)"
    echo "  --dhcp                Use DHCP to obtain IP and gateway for the VPN interface if not set (optional)"
    echo "  -h, --help            Display this help message"
    exit 1
}

# Function to parse command-line arguments
parse_arguments() {
    # Initialize variables
    VPN_INTERFACE=""
    DEFAULT_INTERFACE=""
    DIRECT_DOMAINS=()
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
                IFS=',' read -ra DIRECT_DOMAINS <<< "$2"
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

    # Check required arguments
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
