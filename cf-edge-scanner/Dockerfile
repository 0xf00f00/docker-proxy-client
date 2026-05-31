# CloudflareSpeedTest packaged as a small, multi-arch image.
# No official image exists, so we fetch the release binary for the build arch.
# TCP-ping mode needs no raw sockets / extra caps.
FROM alpine:3.20

ARG CFST_VERSION=2.3.5
# Provided automatically by buildx: amd64 | arm64 | arm
ARG TARGETARCH
ARG TARGETVARIANT

# Release assets are named cfst_linux_<arch>.tar.gz and contain a `cfst` binary.
# openssl powers the TLS-survival gate in scan.sh. (Scheduling lives in the
# ofelia sidecar, so no tzdata is needed here.)
RUN apk add --no-cache ca-certificates curl tar coreutils openssl \
    && case "${TARGETARCH}${TARGETVARIANT}" in \
         amd64)  CF_ARCH=amd64  ;; \
         arm64)  CF_ARCH=arm64  ;; \
         armv7)  CF_ARCH=armv7  ;; \
         arm)    CF_ARCH=armv7  ;; \
         *) echo "unsupported arch: ${TARGETARCH}${TARGETVARIANT}" >&2; exit 1 ;; \
       esac \
    && curl -fsSL --retry 8 --retry-all-errors --retry-delay 5 --connect-timeout 30 \
         "https://github.com/XIU2/CloudflareSpeedTest/releases/download/v${CFST_VERSION}/cfst_linux_${CF_ARCH}.tar.gz" \
         -o /tmp/cfst.tar.gz \
    && tar -xzf /tmp/cfst.tar.gz -C /usr/local/bin cfst \
    && chmod +x /usr/local/bin/cfst \
    && rm -f /tmp/cfst.tar.gz

COPY scan.sh /scan.sh
COPY run.sh /run.sh
RUN chmod +x /scan.sh /run.sh

# Self-loop: scheduled scan + on-demand /out/.scan-now trigger (dashboard writes it).
ENTRYPOINT ["/bin/sh", "/run.sh"]
