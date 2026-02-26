#!/bin/bash
set -euo pipefail

# ─── Configuration ───────────────────────────────────────────────────────────

DNS_FILE="/dns-scanner/active_dns.txt"
REFRESH_INTERVAL="${DNSTT_REFRESH_INTERVAL:-21600}" # Default: 6 hours
LISTEN_ADDR="0.0.0.0:7000"
DEFAULT_FALLBACK_DNS="1.1.1.1:53"

DNSTT_PID=""
CURRENT_PROTO=""
CURRENT_ADDR=""
LOG_FILE="/var/log/dnstt.log"

# Ensure log directory exists (if not /var/log which usually does)
mkdir -p "$(dirname "$LOG_FILE")"
touch "$LOG_FILE"

# ─── Helpers ─────────────────────────────────────────────────────────────────

log()  { echo "[dnstt] $*" >&2; }
die()  { log "ERROR: $*"; exit 1; }

# ─── Validate required environment variables ─────────────────────────────────

for _var in DNSTT_PUBKEY DNSTT_DOMAIN; do
    [ -n "${!_var:-}" ] || die "Required variable $_var is not set."
done

# ─── Health Checks ───────────────────────────────────────────────────────────

# Check that a UDP DNS server responds to a query for DNSTT_DOMAIN.
# Accepts "host" or "host:port" format.
check_dns_udp() {
    local addr="$1"
    local host="${addr%:*}"
    local port="${addr##*:}"
    # If there was no colon, host == port; default to 53
    [[ "$port" == "$host" ]] && port=53

    timeout 2 dig +short +time=2 +tries=1 -p "$port" "@$host" \
        "$DNSTT_DOMAIN" >/dev/null 2>&1
}

# Verify a DoH endpoint actually returns a DNS-JSON response.
check_dns_doh() {
    local url="$1"
    timeout 3 curl -sfS -m 2 --connect-timeout 2 --no-keepalive \
        -H "accept: application/dns-json" \
        "${url}?name=${DNSTT_DOMAIN}&type=A" 2>/dev/null \
        | grep -q '"Status"'
}

# ─── Resolve which DNS address to use ────────────────────────────────────────
# Priority: CoreDNS → DoH env var → UDP env var → active_dns file → fallback

resolve_dns() {
    # 1. DoH environment variable
    if [[ -n "${DNSTT_DOH_URL:-}" ]] && check_dns_doh "$DNSTT_DOH_URL"; then
        echo "DOH $DNSTT_DOH_URL"
        return
    fi

    # 2. CoreDNS service
    if check_dns_udp "coredns"; then
        echo "UDP coredns:53"
        return
    fi

    # 3. UDP resolver environment variable
    if [[ -n "${DNSTT_RESOLVER:-}" ]] && check_dns_udp "$DNSTT_RESOLVER"; then
        echo "UDP $DNSTT_RESOLVER"
        return
    fi

    # 4. Active DNS file (written by dns-scanner)
    if [[ -f "$DNS_FILE" && -s "$DNS_FILE" ]]; then
        local ip
        ip=$(head -1 "$DNS_FILE" | tr -d '[:space:]')
        if check_dns_udp "$ip"; then
            echo "UDP ${ip}:53"
            return
        fi
    fi

    # 5. Fallback – best effort
    local fallback="${DNSTT_RESOLVER:-$DEFAULT_FALLBACK_DNS}"
    log "WARNING: All DNS checks failed, falling back to $fallback"
    echo "UDP $fallback"
}

# ─── Start (or restart) dnstt-client in background ──────────────────────────

start_dnstt() {
    local proto="$1" addr="$2"

    log "Truncating log file: $LOG_FILE"
    : > "$LOG_FILE"

    if [[ "$proto" == "DOH" ]]; then
        log "Starting with DoH: $addr"
        dnstt-client -doh "$addr" -pubkey "$DNSTT_PUBKEY" "$DNSTT_DOMAIN" "$LISTEN_ADDR" 2> >(tee -a "$LOG_FILE" >&2) &
    else
        log "Starting with UDP: $addr"
        dnstt-client -udp "$addr" -pubkey "$DNSTT_PUBKEY" "$DNSTT_DOMAIN" "$LISTEN_ADDR" 2> >(tee -a "$LOG_FILE" >&2) &
    fi

    DNSTT_PID=$!

    # Give the process a moment to fail on obvious errors (bad args, missing binary)
    sleep 1
    if ! kill -0 "$DNSTT_PID" 2>/dev/null; then
        die "dnstt-client failed to start (exited immediately)."
    fi

    CURRENT_PROTO="$proto"
    CURRENT_ADDR="$addr"
    log "Started with PID $DNSTT_PID"
}

# ─── Graceful shutdown ───────────────────────────────────────────────────────

cleanup() {
    if [[ -n "$DNSTT_PID" ]]; then
        log "Shutting down (PID $DNSTT_PID)..."
        kill "$DNSTT_PID" 2>/dev/null || true
        wait "$DNSTT_PID" 2>/dev/null || true
    fi
    exit 0
}
trap cleanup TERM INT HUP

# ─── Main ────────────────────────────────────────────────────────────────────

read -r NEXT_PROTO NEXT_ADDR <<< "$(resolve_dns)"
[[ -n "$NEXT_ADDR" ]] || die "Could not resolve a starting DNS configuration."

start_dnstt "$NEXT_PROTO" "$NEXT_ADDR"

# Periodic refresh loop: restart dnstt if a better/different DNS becomes available
while true; do
    sleep "$REFRESH_INTERVAL" &
    wait $!  # interruptible sleep

    # Restart with same config if dnstt died unexpectedly
    if ! kill -0 "$DNSTT_PID" 2>/dev/null; then
        log "Process died unexpectedly, restarting ($CURRENT_PROTO $CURRENT_ADDR)..."
        start_dnstt "$CURRENT_PROTO" "$CURRENT_ADDR"
        continue
    fi

    # Re-evaluate DNS and swap only on change
    read -r NEXT_PROTO NEXT_ADDR <<< "$(resolve_dns)"

    if [[ "$NEXT_PROTO" != "$CURRENT_PROTO" || "$NEXT_ADDR" != "$CURRENT_ADDR" ]]; then
        log "DNS changed: $CURRENT_PROTO $CURRENT_ADDR → $NEXT_PROTO $NEXT_ADDR"
        kill "$DNSTT_PID" 2>/dev/null || true
        wait "$DNSTT_PID" 2>/dev/null || true
        start_dnstt "$NEXT_PROTO" "$NEXT_ADDR"
    fi
done
