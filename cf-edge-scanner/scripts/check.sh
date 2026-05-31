#!/usr/bin/env bash
# Runs the same checks as the scanner-image CI lint job (gofmt, go vet,
# golangci-lint, go test -race) so failures surface locally, not only on
# GitHub Actions. Keep the golangci-lint version in sync with
# .github/workflows/scanner-image.yml.
set -euo pipefail

GOLANGCI_VERSION="v2.12.2"

cd "$(dirname "$0")/.."  # cf-edge-scanner

# Build golangci-lint with the running toolchain. golangci-lint's own go.mod
# pins an older toolchain, and a linter built with an older Go than the module
# it lints refuses to run -- so we force the local full version (e.g. go1.26.0),
# which necessarily satisfies this module's target.
GO_TOOLCHAIN="$(go env GOVERSION)"
GOBIN="$(go env GOPATH)/bin"
LINT="${GOBIN}/golangci-lint"

have_pinned_lint() {
  [ -x "$LINT" ] && "$LINT" version 2>/dev/null | grep -q "has version ${GOLANGCI_VERSION#v}"
}

if ! have_pinned_lint; then
  echo "==> installing golangci-lint ${GOLANGCI_VERSION} (toolchain ${GO_TOOLCHAIN})"
  GOTOOLCHAIN="${GO_TOOLCHAIN}" go install \
    "github.com/golangci/golangci-lint/v2/cmd/golangci-lint@${GOLANGCI_VERSION}"
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
