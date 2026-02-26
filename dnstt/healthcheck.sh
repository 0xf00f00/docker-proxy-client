#!/bin/sh
set -e

LOG_FILE="/var/log/dnstt.log"
LISTEN_PORT="7000"

# 1. Check if dnstt-client process is running
if ! pidof dnstt-client > /dev/null 2>&1; then
    echo "ERROR: dnstt-client process not found"
    exit 1
fi

# 2. Check if port 7000 is accepting connections
if ! nc -z 127.0.0.1 "$LISTEN_PORT" 2>/dev/null; then
    echo "ERROR: Port $LISTEN_PORT not accepting connections"
    exit 1
fi

# 3. Check logs for critical errors
if [ -f "$LOG_FILE" ]; then
    RECENT=$(tail -n 20 "$LOG_FILE")

    # "read/write on closed pipe" — session is dead and won't recover
    if echo "$RECENT" | grep -q "closed pipe"; then
        echo "ERROR: Detected 'closed pipe' error in logs"
        exit 1
    fi

    # "deadline exceeded" — DNS resolution completely failing
    # Fail if 3 or more occurrences in last 20 lines
    COUNT=$(echo "$RECENT" | grep -c "deadline exceeded" || true)
    if [ "$COUNT" -ge 3 ]; then
        echo "ERROR: Excessive 'deadline exceeded' errors ($COUNT in last 20 lines)"
        exit 1
    fi
fi

exit 0
