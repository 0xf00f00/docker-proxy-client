#!/bin/bash
set -euo pipefail

# Renders /etc/mdns/client_config.toml from env vars at startup and execs the
# MasterDnsVPN client. The resolvers file (client_resolvers.txt) is a separate
# user-editable bind mount; the client reads it from the working directory.

CFG_DIR="/etc/mdns"
TEMPLATE="${CFG_DIR}/client_config.toml.template"
CONFIG="${CFG_DIR}/client_config.toml"
RESOLVERS="${CFG_DIR}/client_resolvers.txt"
EXAMPLE_RESOLVERS="${CFG_DIR}/client_resolvers.example.txt"

log() { echo "[mdns] $*" >&2; }
die() { log "ERROR: $*"; exit 1; }

for v in MDNS_DOMAIN MDNS_ENCRYPTION_KEY; do
    [ -n "${!v:-}" ] || die "Required variable $v is not set."
done

# Seed resolvers from the bundled example if the bind-mounted file is empty
# (Docker creates an empty file when the source on the host is missing).
if [ ! -s "${RESOLVERS}" ]; then
    log "client_resolvers.txt is empty; seeding from example"
    cp "${EXAMPLE_RESOLVERS}" "${RESOLVERS}"
fi

# Escape characters that would break sed's replacement (|, &, \).
sed_escape() { printf '%s' "$1" | sed -e 's/[\\&|]/\\&/g'; }

DOMAIN_ESC=$(sed_escape "${MDNS_DOMAIN}")
KEY_ESC=$(sed_escape "${MDNS_ENCRYPTION_KEY}")
METHOD="${MDNS_DATA_ENCRYPTION_METHOD:-1}"
MIN_UP_MTU="${MDNS_MIN_UPLOAD_MTU:-38}"
MAX_UP_MTU="${MDNS_MAX_UPLOAD_MTU:-150}"
MIN_DOWN_MTU="${MDNS_MIN_DOWNLOAD_MTU:-200}"
MAX_DOWN_MTU="${MDNS_MAX_DOWNLOAD_MTU:-4000}"

sed \
    -e "s|@@DOMAIN@@|${DOMAIN_ESC}|g" \
    -e "s|@@ENCRYPTION_KEY@@|${KEY_ESC}|g" \
    -e "s|@@DATA_ENCRYPTION_METHOD@@|${METHOD}|g" \
    -e "s|@@MIN_UPLOAD_MTU@@|${MIN_UP_MTU}|g" \
    -e "s|@@MAX_UPLOAD_MTU@@|${MAX_UP_MTU}|g" \
    -e "s|@@MIN_DOWNLOAD_MTU@@|${MIN_DOWN_MTU}|g" \
    -e "s|@@MAX_DOWNLOAD_MTU@@|${MAX_DOWN_MTU}|g" \
    "${TEMPLATE}" > "${CONFIG}"

cd "${CFG_DIR}"
exec /usr/local/bin/mdns-client -config "${CONFIG}"
