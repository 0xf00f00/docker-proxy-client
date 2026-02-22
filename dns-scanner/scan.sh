#!/bin/sh

# Use default values if not defined in the environment
CIDR_FILE=${CIDR_FILE:-"/cidrs.txt"}
OUTPUT_FILE=${OUTPUT_FILE:-"/active_dns.txt"}
TARGET_DOMAIN=${TARGET_DOMAIN:-"A,example.com"}
RATE_LIMIT=${RATE_LIMIT:-"5000"}
NUM_RESULTS=${NUM_RESULTS:-"1"}
NETWORK_INTERFACE=${NETWORK_INTERFACE:-"eth0"}

ZMAP_EXTRA_ARGS=${ZMAP_EXTRA_ARGS:-""}
SCAN_INTERVAL=${SCAN_INTERVAL:-"86400"} # Default to 24 hours
RUN_ON_STARTUP=${RUN_ON_STARTUP:-"true"}

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

    # Run zmap with the specified configurations
    # -r <rate>: sets the network rate limit in packets/sec to prevent overloading the network interface
    # -i <interface>: explicitly bind to a physical adapter to bypass host TUN/TAP routes
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

if [ "$RUN_ON_STARTUP" = "true" ]; then
    run_scan
fi

if [ "$SCAN_INTERVAL" -gt 0 ]; then
    while true; do
        if [ "$RUN_ON_STARTUP" = "false" ]; then
            echo "[$(date)] Sleeping for $SCAN_INTERVAL seconds before next scan..."
            sleep "$SCAN_INTERVAL"
            run_scan
        else
            # If we already ran on startup, sleep before the *next* run
            echo "[$(date)] Sleeping for $SCAN_INTERVAL seconds before next scan..."
            sleep "$SCAN_INTERVAL"
            run_scan
        fi
        RUN_ON_STARTUP="false" # Ensure we sleep first on subsequent loop iterations
    done
fi
