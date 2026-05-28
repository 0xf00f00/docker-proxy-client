FROM alpine:3.20

ARG MDNS_VERSION=latest
ARG MDNS_REPO=masterking32/MasterDnsVPN
ARG TARGETARCH
ARG TARGETVARIANT

RUN apk add --no-cache ca-certificates wget unzip bash

RUN set -eux; \
    case "${TARGETARCH:-amd64}" in \
      amd64) ARCH_TAG=Linux_AMD64 ;; \
      arm64) ARCH_TAG=Linux_ARM64 ;; \
      arm) \
        case "${TARGETVARIANT}" in \
          v6) ARCH_TAG=Linux_ARMV6 ;; \
          v7|"") ARCH_TAG=Linux_ARMV7 ;; \
          *) echo "Unsupported ARM variant: ${TARGETVARIANT}" >&2; exit 1 ;; \
        esac ;; \
      *) echo "Unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    if [ "${MDNS_VERSION}" = "latest" ]; then \
      URL="https://github.com/${MDNS_REPO}/releases/latest/download/MasterDnsVPN_Client_${ARCH_TAG}.zip"; \
    else \
      URL="https://github.com/${MDNS_REPO}/releases/download/${MDNS_VERSION}/MasterDnsVPN_Client_${ARCH_TAG}.zip"; \
    fi; \
    wget -q "${URL}" -O /tmp/mdns.zip; \
    unzip -q /tmp/mdns.zip -d /tmp/mdns; \
    BIN="$(find /tmp/mdns -type f -name "MasterDnsVPN_Client_${ARCH_TAG}_v*" | sort -V | tail -n1)"; \
    test -n "${BIN}" || { echo "Client binary not found in archive" >&2; exit 1; }; \
    install -m 0755 "${BIN}" /usr/local/bin/mdns-client; \
    rm -rf /tmp/mdns.zip /tmp/mdns

WORKDIR /etc/mdns

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY client_config.toml.template /etc/mdns/client_config.toml.template
COPY client_resolvers.example.txt /etc/mdns/client_resolvers.example.txt
RUN chmod 0755 /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
