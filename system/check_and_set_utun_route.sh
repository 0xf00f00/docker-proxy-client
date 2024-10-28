#!/bin/bash
# Minimal script to check and add default route for utun interface

# Check if utun interface is up
if ip link show utun up > /dev/null 2>&1; then
    # If the default route via utun doesn't exist, add it
    if ! ip route | grep -q "default.*dev utun"; then
        ip route add default dev utun
        echo "Default route added via utun interface."
    else
        echo "Default route via utun is already set."
    fi
    exit 0  # Success
else
    echo "utun interface is not up. Exiting with failure."
    exit 1  # Failure if interface is not up
fi
