#!/bin/sh
set -u

# Purely trigger-driven: this loop only reacts to files dropped in /out --
# scheduling lives outside (the ofelia sidecar writes .scan-now weekly,
# the dashboard writes it on demand). No internal timer, so the heavy sweep
# never fires on its own and never competes with normal traffic unprompted.
TICK=${TICK:-5}
TEST_PINGS=${TEST_PINGS:-30}


SCAN_TIMEOUT=${SCAN_TIMEOUT:-21600}  # 6 h hard cap on a full scan (backstop only)
TEST_TIMEOUT=${TEST_TIMEOUT:-180}    # 3 min hard cap on a single-IP probe

HEARTBEAT_SEC=${HEARTBEAT_SEC:-10}
STOP_POLL=${STOP_POLL:-2}     # how often a running scan checks for a stop request
TRIGGER=/out/.scan-now
CANCEL=/out/.scan-stop        # dashboard drops this to stop an in-flight scan
TEST_REQ=/out/.test-request
LAST=/out/.last-scan          # epoch of last scan -- read by the dashboard
SCANNING=/out/.scanning
TESTING=/out/.testing
RESULTS=/out/test-results.txt

ts() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }

# Touch $1 every HEARTBEAT_SEC until killed
heartbeat() {
  while :; do
    sleep "$HEARTBEAT_SEC"
    touch "$1" 2>/dev/null || break
  done
}

# Replace any prior row for $1 and append a fresh one.
record() {
  tmp=$(mktemp)
  grep -v "^$1 " "$RESULTS" 2>/dev/null >"$tmp" || true
  echo "$1 $2 $3 $4 $5 $(date +%s)" >>"$tmp"
  mv "$tmp" "$RESULTS"
}

scan() {
  : >"$SCANNING"
  rm -f "$CANCEL"   # clear any leftover stop request from a queued-but-skipped scan
  # Run the sweep in the background so we can watch for a stop request while it
  # works. We touch $SCANNING ourselves each tick (no separate heartbeat proc).
  timeout "$SCAN_TIMEOUT" /scan.sh &
  spid=$!
  cancelled=0
  while kill -0 "$spid" 2>/dev/null; do
    touch "$SCANNING" 2>/dev/null
    if [ -f "$CANCEL" ]; then
      cancelled=1
      echo "[$(ts)] cf-edge-scanner: stop requested -- terminating scan" >&2
      # Queue scan.sh's TERM trap, then drop the in-flight cfst child (a TERM
      # while cfst runs in the foreground only fires the trap once cfst exits).
      kill -TERM "$spid" 2>/dev/null
      pkill -TERM -f 'cfst -f' 2>/dev/null
      i=0
      while kill -0 "$spid" 2>/dev/null && [ "$i" -lt 5 ]; do sleep 1; i=$((i + 1)); done
      kill -KILL "$spid" 2>/dev/null
      pkill -KILL -f 'cfst -f' 2>/dev/null
      break
    fi
    sleep "$STOP_POLL"
  done
  wait "$spid" 2>/dev/null
  rc=$?
  if [ "$cancelled" = 1 ]; then
    echo "[$(ts)] cf-edge-scanner: scan stopped by request; previous pool kept" >&2
  elif [ "$rc" = 0 ]; then
    date +%s >"$LAST"
  else
    echo "[$(ts)] cf-edge-scanner: scan failed or timed out (>${SCAN_TIMEOUT}s); previous pool kept" >&2
  fi
  rm -f "$SCANNING" "$CANCEL"
}

# Reliability probe of one edge IP: TEST_PINGS TCP connects, record loss + latency.
test_ip() {
  ip=$1
  echo "$ip" >"$TESTING"
  heartbeat "$TESTING" & hb=$!
  timeout "$TEST_TIMEOUT" cfst -ip "$ip" -t "$TEST_PINGS" -dd -tlr 1 -tl 9999 -p 1 -o /tmp/test.csv >/dev/null 2>&1 || true
  row=$(tail -n +2 /tmp/test.csv 2>/dev/null | grep -F "$ip," | head -1)
  if [ -n "$row" ]; then
    sent=$(echo "$row" | cut -d, -f2); recv=$(echo "$row" | cut -d, -f3)
    loss=$(echo "$row" | cut -d, -f4); lat=$(echo "$row" | cut -d, -f5)
  else
    # cfst produced nothing (unreachable / timed out)
    sent=$TEST_PINGS; recv=0; loss=1.00; lat=0
  fi
  record "$ip" "$sent" "$recv" "$loss" "$lat"
  kill "$hb" 2>/dev/null; wait "$hb" 2>/dev/null
  rm -f "$TESTING" /tmp/test.csv
}

# Crash recovery on (re)start
if [ -f "$TESTING" ]; then
  rip=$(cat "$TESTING" 2>/dev/null)
  [ -n "$rip" ] && { echo "[$(ts)] cf-edge-scanner: recovering interrupted test $rip -> failed"; record "$rip" "$TEST_PINGS" 0 1.00 0; }
fi
rm -f "$SCANNING" "$TESTING" "$CANCEL"

while true; do
  if [ -f "$TEST_REQ" ]; then
    ip=$(cat "$TEST_REQ" 2>/dev/null); rm -f "$TEST_REQ"
    [ -n "$ip" ] && { echo "[$(ts)] cf-edge-scanner: test $ip"; test_ip "$ip"; }
  elif [ -f "$TRIGGER" ]; then
    rm -f "$TRIGGER"
    echo "[$(ts)] cf-edge-scanner: scan triggered (cron sidecar or dashboard)"
    scan
  fi
  sleep "$TICK"
done
