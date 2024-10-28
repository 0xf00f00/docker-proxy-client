#!/bin/bash
# Script to check and add default route for vpn_vpn interface with gateway IP,
# and request an IP via DHCP if none is assigned.

# Check if vpn_vpn interface is up
if ip link show vpn_vpn up > /dev/null 2>&1; then

    # Check if vpn_vpn interface has an IP address assigned
    IP_ASSIGNED=$(ip addr show vpn_vpn | grep -w inet | awk '{print $2}')
    
    # If no IP is assigned, call DHCP client
    if [ -z "$IP_ASSIGNED" ]; then
        echo "No IP assigned to vpn_vpn. Requesting IP via DHCP..."
        dhclient vpn_vpn
        # Wait a moment to ensure IP is assigned
        sleep 2
        # Re-check if IP is assigned
        IP_ASSIGNED=$(ip addr show vpn_vpn | grep -w inet | awk '{print $2}')
        
        if [ -z "$IP_ASSIGNED" ]; then
            echo "Failed to obtain IP for vpn_vpn interface. Exiting."
            exit 1
        else
            echo "IP assigned to vpn_vpn: $IP_ASSIGNED"
        fi
    else
        echo "vpn_vpn already has IP assigned: $IP_ASSIGNED"
    fi

    # Check if a default route exists and retrieve the current default gateway IP
    DEFAULT_GW=$(ip route | awk '/default/ {print $3}' | head -n 1)

    if [ -z "$DEFAULT_GW" ]; then
        echo "No default gateway detected. Exiting."
        exit 1
    fi

    # If the default route via vpn_vpn with a gateway doesn't exist, add it
    if ! ip route | grep -q "default.*dev vpn_vpn"; then
        ip route add default via "$DEFAULT_GW" dev vpn_vpn
        echo "Default route added via vpn_vpn interface with gateway $DEFAULT_GW."
    else
        echo "Default route via vpn_vpn is already set."
    fi
    exit 0  # Success
else
    echo "vpn_vpn interface is not up. Exiting with failure."
    exit 1  # Failure if interface is not up
fi
