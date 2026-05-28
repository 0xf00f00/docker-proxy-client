FROM alpine:3.20

ARG GOOSE_VERSION=v1.7.1
ARG TARGETARCH

RUN apk add --no-cache ca-certificates wget tar

RUN set -eux; \
    case "${TARGETARCH:-amd64}" in \
      amd64) ARCH=amd64 ;; \
      arm64) ARCH=arm64 ;; \
      arm)   ARCH=armv7 ;; \
      *) echo "unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    URL="https://github.com/Kianmhz/GooseRelayVPN/releases/download/${GOOSE_VERSION}/GooseRelayVPN-client-${GOOSE_VERSION}-linux-${ARCH}.tar.gz"; \
    wget -q "${URL}" -O /tmp/goose.tar.gz; \
    tar -xzf /tmp/goose.tar.gz -C /tmp; \
    find /tmp -type f -name 'goose-client' -exec install -m 0755 {} /usr/local/bin/goose-client \; ; \
    test -x /usr/local/bin/goose-client; \
    rm -rf /tmp/goose.tar.gz /tmp/GooseRelayVPN-*

ENTRYPOINT ["/usr/local/bin/goose-client"]
CMD ["-config", "/etc/goose-relay/client_config.json"]
