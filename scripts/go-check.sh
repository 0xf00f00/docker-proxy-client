#!/usr/bin/env bash
# Runs a Go module's CI checks (gofmt, go vet, golangci-lint, go test -race) locally.
# usage: scripts/go-check.sh <module-dir>   e.g. cf-edge-manager
# Keep GOLANGCI_VERSION in sync with .github/workflows/{manager,dns-scanner}-image.yml.
set -euo pipefail

GOLANGCI_VERSION="v2.13.2"

cd "$(dirname "$0")/../${1:?usage: go-check.sh <module-dir>}"

# Build the linter with the running toolchain: a linter built with an older Go than the module it lints refuses to run.
GO_TOOLCHAIN="$(go env GOVERSION)"
LINT="$(go env GOPATH)/bin/golangci-lint"

if ! { [ -x "$LINT" ] && "$LINT" version 2>/dev/null | grep -q "has version ${GOLANGCI_VERSION#v}"; }; then
  echo "==> installing golangci-lint ${GOLANGCI_VERSION} (toolchain ${GO_TOOLCHAIN})"
  GOTOOLCHAIN="${GO_TOOLCHAIN}" go install "github.com/golangci/golangci-lint/v2/cmd/golangci-lint@${GOLANGCI_VERSION}"
fi

echo "==> gofmt"
unformatted="$(gofmt -l .)"
if [ -n "$unformatted" ]; then
  echo "gofmt needed on:"; echo "$unformatted"
  echo "run: gofmt -w ."
  exit 1
fi

echo "==> go vet"
go vet ./...

echo "==> golangci-lint"
GOTOOLCHAIN="${GO_TOOLCHAIN}" "$LINT" run

echo "==> go test -race"
go test -race ./...

echo "==> all checks passed"
