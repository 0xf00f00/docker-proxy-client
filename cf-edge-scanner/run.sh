#!/bin/sh
set -u

# Purely trigger-driven: this loop only reacts to files dropped in /out --
# scheduling lives outside (the ofelia sidecar writes .scan-now weekly,
# the dashboard writes it on demand). No internal timer, so the heavy sweep
# never fires on its own and never competes with normal traffic unprompted.
TICK=${TICK:-5}
TEST_PINGS=${TEST_PINGS:-30}
TRIGGER=/out/.scan-now
TEST_REQ=/out/.test-request
LAST=/out/.last-scan          # epoch of last scan -- read by the dashboard
SCANNING=/out/.scanning
TESTING=/out/.testing
RESULTS=/out/test-results.txt

ts() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }

scan() {
  : >"$SCANNING"
  if /scan.sh; then date +%s >"$LAST"; fi
  rm -f "$SCANNING"
}

# Reliability probe of one edge IP: TEST_PINGS TCP connects, record loss + latency.
test_ip() {
  ip=$1
  echo "$ip" >"$TESTING"
  cfst -ip "$ip" -t "$TEST_PINGS" -dd -tlr 1 -tl 9999 -p 1 -o /tmp/test.csv >/dev/null 2>&1 || true
  row=$(tail -n +2 /tmp/test.csv 2>/dev/null | grep -F "$ip," | head -1)
  if [ -n "$row" ]; then
    sent=$(echo "$row" | cut -d, -f2); recv=$(echo "$row" | cut -d, -f3)
    loss=$(echo "$row" | cut -d, -f4); lat=$(echo "$row" | cut -d, -f5)
  else
    sent=$TEST_PINGS; recv=0; loss=1.00; lat=0
  fi
  tmp=$(mktemp)
  grep -v "^$ip " "$RESULTS" 2>/dev/null >"$tmp" || true
  echo "$ip $sent $recv $loss $lat $(date +%s)" >>"$tmp"
  mv "$tmp" "$RESULTS"
  rm -f "$TESTING" /tmp/test.csv
}

rm -f "$SCANNING" "$TESTING"

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
