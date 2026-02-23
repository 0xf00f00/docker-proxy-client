#!/bin/sh

# Use default values if not defined in the environment
CIDR_FILE=${CIDR_FILE:-"/cidrs.txt"}
OUTPUT_FILE=${OUTPUT_FILE:-"/active_dns.txt"}
TARGET_DOMAIN=${TARGET_DOMAIN:-"A,example.com"}
RATE_LIMIT=${RATE_LIMIT:-"5000"}
NUM_RESULTS=${NUM_RESULTS:-"1"}
NETWORK_INTERFACE=${NETWORK_INTERFACE:-"eth0"}

ZMAP_EXTRA_ARGS=${ZMAP_EXTRA_ARGS:-""}

# Trap SIGTERM and SIGINT to exit immediately when docker stops the container
trap "echo '[$(date)] Received termination signal. Exiting...'; exit 0" TERM INT

run_scan() {
    echo "[$(date)] Starting DNS scanner..."
    echo "CIDR_FILE: $CIDR_FILE"
    echo "OUTPUT_FILE: $OUTPUT_FILE"
    echo "TARGET_DOMAIN: $TARGET_DOMAIN"
    echo "RATE_LIMIT: $RATE_LIMIT (packets/sec)"

    if [ ! -f "$CIDR_FILE" ]; then
        echo "Error: CIDR file not found at $CIDR_FILE"
        exit 1
    fi

    # Automate gateway discovery and ARP priming
    # Dependency-free gateway and MAC discovery to force Ethernet layer
    GW_IP=$(ip route show dev "$NETWORK_INTERFACE" 2>/dev/null | awk '/via/ {print $3; exit}')
    [ -z "$GW_IP" ] && GW_IP=$(route -n 2>/dev/null | awk '$8=="'$NETWORK_INTERFACE'" && ($1=="0.0.0.0"||$1=="default") {print $2; exit}')
    if [ -z "$GW_IP" ] && [ -f /proc/net/route ]; then
        H=$(awk '$1=="'$NETWORK_INTERFACE'" && $2=="00000000" {print $3; exit}' /proc/net/route)
        [ -n "$H" ] && GW_IP=$(printf "%d.%d.%d.%d" 0x$(echo $H|cut -c7-8) 0x$(echo $H|cut -c5-6) 0x$(echo $H|cut -c3-4) 0x$(echo $H|cut -c1-2) 2>/dev/null)
    fi

    if [ -n "$GW_IP" ]; then
        ping -c 1 -W 1 "$GW_IP" >/dev/null 2>&1
        GW_MAC=$(ip neighbor show "$GW_IP" 2>/dev/null | awk '{print $5}')
        [ -z "$GW_MAC" ] && GW_MAC=$(awk '$1=="'$GW_IP'" {print $4; exit}' /proc/net/arp 2>/dev/null)
        if [ -n "$GW_MAC" ] && [ "$GW_MAC" != "00:00:00:00:00:00" ]; then
            echo "[$(date)] Forcing Ethernet layer: Gateway $GW_IP is at $GW_MAC"
            ZMAP_EXTRA_ARGS="$ZMAP_EXTRA_ARGS -G $GW_MAC"
        fi
    fi

    # Run zmap with the specified configurations
    # -r <rate>: sets the network rate limit in packets/sec to prevent overloading the network interface
    # -i <interface>: explicitly bind to a physical adapter
    # -M dns: sets the probe module to DNS
    zmap -p 53 -i "$NETWORK_INTERFACE" -M dns --probe-args="$TARGET_DOMAIN" --output-module=json \
         --output-fields=saddr,dns_answers --output-filter="app_success=1 && dns_ancount>0" \
         -w "$CIDR_FILE" -r "$RATE_LIMIT" $ZMAP_EXTRA_ARGS \
    | grep --line-buffered '"type_str":"A"' \
    | sed -n 's/.*"saddr":"\([^"]*\)".*/\1/p' \
    | head -n "$NUM_RESULTS" > "$OUTPUT_FILE"

    if [ -s "$OUTPUT_FILE" ]; then
        echo "[$(date)] Scan successful. Found active DNS:"
        cat "$OUTPUT_FILE"
    else
        echo "[$(date)] Scan completed but no active DNS found."
    fi
}

run_scan
