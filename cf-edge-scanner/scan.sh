#!/bin/sh
# One scan: TCP-ping a curated set of Cloudflare edges, rank by packet loss then
# latency, write the top IPs to pool.txt. Loss is the signal that matters (a
# "clean" edge can still be heavily throttled); -dd drops the only bandwidth-
# heavy phase. On an empty/failed scan we keep the previous winners.
set -eu

RANGES=${RANGES:-/cf-ranges.txt}
OUT_DIR=${OUT_DIR:-/out}
THREADS=${THREADS:-5}
PING_COUNT=${PING_COUNT:-10}
PORT=${PORT:-443}
LOSS_MAX=${LOSS_MAX:-0.10}
LAT_MAX=${LAT_MAX:-1000}
POOL_SIZE=${POOL_SIZE:-10}

# TLS-survival gate (post-rank): a clean TCP ping only proves the edge answers,
# not that DPI lets a real encrypted session live. For each top candidate we
# open a TLS session, hold it idle (DPI commonly resets idle TLS), then send one
# tiny request and require a valid Cloudflare trace back. Sequential, spaced,
# ~1 KB per check -- so it never disturbs normal traffic.
# Default OFF: this probe is a NAKED handshake
TLS_CHECK=${TLS_CHECK:-0} # set to 1 to enable; requires openssl in PATH
TLS_SNI=${TLS_SNI:-hcaptcha.com}
TLS_HOLD=${TLS_HOLD:-3}        # idle seconds after handshake before the request
TLS_TIMEOUT=${TLS_TIMEOUT:-10} # hard cap per candidate
TLS_GAP=${TLS_GAP:-2}          # polite pause between candidates

RESULT_CSV="$OUT_DIR/result.csv"
POOL="$OUT_DIR/pool.txt"
CLEAN="$OUT_DIR/clean_ip.txt"

log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*"; }
trap 'log "received termination signal, exiting"; exit 0' TERM INT

# Open TLS to ip:PORT, sit idle TLS_HOLD seconds, then send one /cdn-cgi/trace
# request and require a valid trace ("fl=") in the reply. A DPI box that resets
# idle encrypted sessions (or blocks the edge for real traffic) yields no trace,
# so the IP is dropped. ~1 KB total; capped by `timeout`.
tls_ok() {
  ip=$1
  { sleep "$TLS_HOLD"
    printf 'GET /cdn-cgi/trace HTTP/1.1\r\nHost: %s\r\nUser-Agent: curl\r\nConnection: close\r\n\r\n' "$TLS_SNI"
    sleep 2
  } | timeout "$TLS_TIMEOUT" openssl s_client -connect "$ip:$PORT" -servername "$TLS_SNI" -quiet 2>/dev/null \
    | grep -q 'fl='
}

[ -f "$RANGES" ] || { log "ERROR: ranges file not found at $RANGES"; exit 1; }
mkdir -p "$OUT_DIR"

# cfst's -f file is CIDR-only and aborts on any non-CIDR line, so distil our
# documented cf-ranges.txt to bare CIDRs first.
CIDRS="$(mktemp)"
trap 'rm -f "$CIDRS"' EXIT
sed 's/#.*//' "$RANGES" | grep -oE '[0-9]{1,3}(\.[0-9]{1,3}){3}/[0-9]{1,2}' > "$CIDRS" || true
n_cidrs="$(grep -c . "$CIDRS" || echo 0)"
[ "$n_cidrs" -gt 0 ] || { log "ERROR: no valid CIDRs parsed from $RANGES"; exit 1; }

log "scanning $n_cidrs CIDR range(s): threads=$THREADS ping=$PING_COUNT port=$PORT loss<=$LOSS_MAX lat<=${LAT_MAX}ms download=OFF"

cfst -f "$CIDRS" -o "$RESULT_CSV" -dd -n "$THREADS" -t "$PING_COUNT" \
  -tp "$PORT" -tlr "$LOSS_MAX" -tl "$LAT_MAX" -p "$POOL_SIZE" \
  || log "cfst exited non-zero (parsing whatever it wrote)"

# result.csv: IP, Sent, Received, LossRate, AvgLatency, Speed -- rows already sorted best-first.
if [ -s "$RESULT_CSV" ] && [ "$(tail -n +2 "$RESULT_CSV" | grep -c .)" -gt 0 ]; then
  tmp="$(mktemp)"
  tail -n +2 "$RESULT_CSV" | cut -d',' -f1 | grep -E '^[0-9]+\.' | head -n "$POOL_SIZE" > "$tmp"
  if [ -s "$tmp" ]; then
    mv "$tmp" "$POOL"
    head -n 1 "$POOL" > "$CLEAN"
    log "scan OK: $(grep -c . "$POOL") candidate(s); best=$(cat "$CLEAN")"
  else
    rm -f "$tmp"
    log "WARN: no IPs passed loss/latency filters; keeping previous winners"
  fi
else
  log "WARN: empty result.csv; keeping previous winners"
fi

# --- TLS-survival gate: filter the ranked pool to edges that carry real TLS ---
if [ "$TLS_CHECK" = "1" ] && [ -s "$POOL" ]; then
  if command -v openssl >/dev/null 2>&1; then
    survivors="$(mktemp)"
    log "TLS-survival gate: probing $(grep -c . "$POOL") candidate(s) (sni=$TLS_SNI hold=${TLS_HOLD}s, sequential)"
    while IFS= read -r ip; do
      [ -n "$ip" ] || continue
      if tls_ok "$ip"; then
        echo "$ip" >> "$survivors"
        log "  TLS OK   $ip"
      else
        log "  TLS FAIL $ip (dropped)"
      fi
      sleep "$TLS_GAP"
    done < "$POOL"
    if [ -s "$survivors" ]; then
      mv "$survivors" "$POOL"
      head -n 1 "$POOL" > "$CLEAN"
      log "TLS gate: $(grep -c . "$POOL") edge(s) survived; best=$(cat "$CLEAN")"
    else
      rm -f "$survivors"
      log "WARN: no candidate survived TLS gate; keeping TCP-ranked pool as-is"
    fi
  else
    log "WARN: openssl not found; skipping TLS-survival gate"
  fi
fi
