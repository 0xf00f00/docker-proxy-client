#!/bin/sh
# Renders a sing-box config at runtime (env vars from .env) and runs sing-box.
# Provides a SOCKS5 inbound on :1080 backed by a naive outbound with sing-box
# UoT v2 framing so both TCP and UDP work through the naive tunnel.
# Server side must speak the same UoT framing — see Lintech-1's recipe in
# https://github.com/klzgrad/naiveproxy/issues/617#issuecomment-4388472496
set -e

NAIVE_SERVER_CLEAN="${NAIVE_SERVER#https://}"
NAIVE_SERVER_CLEAN="${NAIVE_SERVER_CLEAN#http://}"
case "$NAIVE_SERVER_CLEAN" in
  *:*) HOST="${NAIVE_SERVER_CLEAN%:*}"; PORT="${NAIVE_SERVER_CLEAN##*:}" ;;
  *)   HOST="$NAIVE_SERVER_CLEAN";       PORT="443" ;;
esac

mkdir -p /tmp/sing-box
cat > /tmp/sing-box/config.json <<EOF
{
  "log": { "level": "warn" },
  "inbounds": [
    {
      "type": "socks",
      "tag": "socks-in",
      "listen": "0.0.0.0",
      "listen_port": 1080
    }
  ],
  "outbounds": [
    {
      "type": "naive",
      "tag": "naive-out",
      "server": "$HOST",
      "server_port": $PORT,
      "username": "$NAIVE_USER",
      "password": "$NAIVE_PASSWORD",
      "extra_headers": {
        "Tunnel-Mode": "udp"
      },
      "udp_over_tcp": {
        "enabled": true,
        "version": 2
      },
      "tls": {
        "enabled": true,
        "server_name": "$HOST"
      }
    },
    {
      "type": "direct",
      "tag": "direct"
    }
  ],
  "route": {
    "final": "naive-out"
  }
}
EOF

exec sing-box -C /tmp/sing-box/ run
