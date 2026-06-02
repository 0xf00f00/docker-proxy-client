# syntax=docker/dockerfile:1

# --- build ---
FROM --platform=$BUILDPLATFORM golang:1.26-alpine3.23 AS build
WORKDIR /src

# Resolve modules first so this layer is cached until go.mod/go.sum change.
COPY go.mod go.su[m] ./
RUN --mount=type=cache,target=/go/pkg/mod \
    go mod download

COPY . .
ARG TARGETOS TARGETARCH
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH \
    go build -trimpath -buildvcs=false -ldflags="-s -w" \
      -o /out/manager ./cmd/manager

# --- cfst: fetch the prebuilt CloudflareSpeedTest binary for the target arch ---
FROM --platform=$BUILDPLATFORM alpine:3.23 AS cfst
ARG CFST_VERSION=2.3.5
ARG TARGETARCH TARGETVARIANT
RUN apk add --no-cache curl tar \
    && case "${TARGETARCH}${TARGETVARIANT}" in \
         amd64) CF_ARCH=amd64 ;; \
         arm64) CF_ARCH=arm64 ;; \
         armv7) CF_ARCH=armv7 ;; \
         arm)   CF_ARCH=armv7 ;; \
         *) echo "unsupported arch: ${TARGETARCH}${TARGETVARIANT}" >&2; exit 1 ;; \
       esac \
    && curl -fsSL --retry 8 --retry-all-errors --retry-delay 5 --connect-timeout 30 \
         "https://github.com/XIU2/CloudflareSpeedTest/releases/download/v${CFST_VERSION}/cfst_linux_${CF_ARCH}.tar.gz" \
         -o /tmp/cfst.tar.gz \
    && tar -xzf /tmp/cfst.tar.gz -C /usr/local/bin cfst \
    && chmod +x /usr/local/bin/cfst

# --- xray: the real-path probe's transport (vless/xHTTP, chrome uTLS). Pinned to
# the SAME image the live proxy runs so the probe's wire behaviour matches prod.
FROM ghcr.io/xtls/xray-core:latest AS xray

FROM alpine:3.23
RUN apk add --no-cache ca-certificates tini curl iptables iproute2 tzdata
COPY --from=cfst  /usr/local/bin/cfst /usr/local/bin/cfst
COPY --from=xray  /usr/local/bin/xray /usr/local/bin/xray
COPY --from=build /out/manager        /usr/local/bin/manager
COPY cf-ranges.txt /cf-ranges.txt

EXPOSE 8088
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8088/healthz || exit 1

ENTRYPOINT ["/sbin/tini", "--", "/usr/local/bin/manager"]
