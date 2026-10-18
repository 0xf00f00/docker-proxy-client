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
# Upstream re-uploads assets under the same tag: pin sha256, find the binary anywhere.
RUN apk add --no-cache curl tar \
    && case "${TARGETARCH}${TARGETVARIANT}" in \
         amd64) CF_ARCH=amd64 CF_SHA=c4c8fc76b4e1bf2bdb5ced8b765956d82dda7bc4eb59df5c04053f0f7db98d90 ;; \
         arm64) CF_ARCH=arm64 CF_SHA=0ac992fcf24d4684caed33620deb9b83ce82f32d2418dc1f90be490ce5900300 ;; \
         armv7|arm) CF_ARCH=armv7 CF_SHA=73386234fe07a766071709859168e19b37f6878345317ccaa992efe35f4ef5f0 ;; \
         *) echo "unsupported arch: ${TARGETARCH}${TARGETVARIANT}" >&2; exit 1 ;; \
       esac \
    && curl -fsSL --retry 8 --retry-all-errors --retry-delay 5 --connect-timeout 30 \
         "https://github.com/XIU2/CloudflareSpeedTest/releases/download/v${CFST_VERSION}/cfst_linux_${CF_ARCH}.tar.gz" \
         -o /tmp/cfst.tar.gz \
    && echo "${CF_SHA}  /tmp/cfst.tar.gz" | sha256sum -c - \
    && mkdir /tmp/cfst && tar -xzf /tmp/cfst.tar.gz -C /tmp/cfst \
    && BIN="$(find /tmp/cfst -type f -name cfst | head -n1)" \
    && test -n "${BIN}" \
    && install -m 0755 "${BIN}" /usr/local/bin/cfst

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
