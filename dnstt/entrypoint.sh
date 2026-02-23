#!/bin/bash
set -e

# ─── Configuration ───────────────────────────────────────────────────────────
DNS_FILE="/dns-scanner/active_dns.txt"
REFRESH_INTERVAL="${DNSTT_REFRESH_INTERVAL:-21600}" # Default: 6 hours
LISTEN_ADDR="0.0.0.0:7000"

DNSTT_PID=""
CURRENT_DNS_ADDR=""

# ─── Resolve which DNS address to use ────────────────────────────────────────
resolve_dns_addr() {
    # Check if file exists, is a regular file (-f), and has content (-s)
    if [ -f "$DNS_FILE" ] && [ -s "$DNS_FILE" ]; then
        local ip
        ip=$(head -1 "$DNS_FILE" | tr -d '[:space:]')
        printf '%s:53' "$ip"
    else
        printf '%s' "$DNSTT_RESOLVER"
    fi
}

# ─── Start (or restart) dnstt-client in background ───────────────────────────
start_dnstt() {
    local addr="$1"
    
    if [ -n "$DNSTT_DOH_URL" ]; then
        echo "[dnstt] Starting with DoH: $DNSTT_DOH_URL"
        dnstt-client -doh "$DNSTT_DOH_URL" -pubkey "$DNSTT_PUBKEY" "$DNSTT_DOMAIN" "$LISTEN_ADDR" &
    else
        echo "[dnstt] Starting with UDP: $addr"
        dnstt-client -udp "$addr" -pubkey "$DNSTT_PUBKEY" "$DNSTT_DOMAIN" "$LISTEN_ADDR" &
    fi

    DNSTT_PID=$!
    CURRENT_DNS_ADDR="$addr"
    echo "[dnstt] Started with PID $DNSTT_PID"
}

# ─── Graceful shutdown ────────────────────────────────────────────────────────
cleanup() {
    echo "[dnstt] Shutting down (PID $DNSTT_PID)..."
    kill "$DNSTT_PID" 2>/dev/null
    wait "$DNSTT_PID" 2>/dev/null
    exit 0
}
trap cleanup TERM INT

# ─── Main ─────────────────────────────────────────────────────────────────────
CURRENT_DNS_ADDR=$(resolve_dns_addr)
if [ -z "$CURRENT_DNS_ADDR" ]; then
    echo "[dnstt] ERROR: No DNS resolver available. Set DNSTT_RESOLVER or ensure $DNS_FILE exists."
    exit 1
fi

start_dnstt "$CURRENT_DNS_ADDR"

# Periodic refresh loop: restart dnstt if the file content changes
while true; do
    sleep "$REFRESH_INTERVAL" &
    wait $! # interruptible sleep

    # Check if the dnstt process is still alive
    if ! kill -0 "$DNSTT_PID" 2>/dev/null; then
        echo "[dnstt] Process died unexpectedly, restarting with same address..."
        start_dnstt "$CURRENT_DNS_ADDR"
        continue
    fi

    echo "[dnstt] Checking for DNS updates in $DNS_FILE..."
    NEW_ADDR=$(resolve_dns_addr)

    if [ -z "$NEW_ADDR" ] || [ "$NEW_ADDR" = "$CURRENT_DNS_ADDR" ]; then
        echo "[dnstt] No DNS update needed (still using $CURRENT_DNS_ADDR)"
    else
        echo "[dnstt] DNS update found: $CURRENT_DNS_ADDR → $NEW_ADDR. Restarting..."
        kill "$DNSTT_PID" 2>/dev/null
        wait "$DNSTT_PID" 2>/dev/null
        start_dnstt "$NEW_ADDR"
    fi
done
