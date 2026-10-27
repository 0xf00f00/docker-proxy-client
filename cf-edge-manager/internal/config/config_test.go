package config

import (
	"testing"
)

func TestSelectNeedsTheWireIdentity(t *testing.T) {
	t.Setenv("SELECT_ENABLE", "true")
	if _, err := Load(); err == nil {
		t.Error("selection without PROBE_UUID would read every edge as failed: refuse to start")
	}
}
