# syntax=docker/dockerfile:1

FROM golang:1.25-alpine AS build
WORKDIR /src
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -o /out/scanner ./cmd/scanner

FROM alpine:3.20
RUN apk add --no-cache ca-certificates wget unzip curl bash

ARG MDNS_VERSION=""
ARG MDNS_REPO=oiyoa/mdns
ARG MDNS_SHA256=""
ARG TARGETARCH
ARG TARGETVARIANT
RUN set -eux; \
    if [ -z "${MDNS_VERSION}" ]; then \
      echo ">> MDNS_VERSION empty — building WITHOUT probe binary (scanner = gates-only)"; \
    else \
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
      echo ">> fetching ${URL}"; \
      wget -q "${URL}" -O /tmp/mdns.zip; \
      if [ -n "${MDNS_SHA256}" ]; then echo "${MDNS_SHA256}  /tmp/mdns.zip" | sha256sum -c -; fi; \
      unzip -q /tmp/mdns.zip -d /tmp/mdns; \
      BIN="$(find /tmp/mdns -type f -name "MasterDnsVPN_Client_${ARCH_TAG}_v*" | sort -V | tail -n1)"; \
      test -n "${BIN}" || { echo "Client binary not found in archive" >&2; exit 1; }; \
      install -m 0755 "${BIN}" /usr/local/bin/mdns-client; \
      rm -rf /tmp/mdns.zip /tmp/mdns; \
    fi

WORKDIR /app
COPY --from=build /out/scanner /usr/local/bin/scanner
COPY data /app/data
ENV SCANNER_DATA_DIR=/app/data
ENV MDNS_PROBE_BIN=/usr/local/bin/mdns-client
ENTRYPOINT ["scanner"]
