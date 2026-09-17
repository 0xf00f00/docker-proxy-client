package realpath

import (
	"errors"
	"testing"
)

// A pre-gate failure means the edge is unusable. Regression guard: this once
// returned Survived=true (variable named `fail` assigned to `Survived`), which
// made every burned edge report as healthy and pinned the selector to dead IPs.
func TestFailedVerdictIsNotSurvived(t *testing.T) {
	r := failedVerdict("192.0.2.1", 4, errors.New("connection reset by peer"))

	if r.Survived == nil {
		t.Fatal("Survived is nil: a demonstrated failure must be a verdict, not 'no signal' (the selector keeps the edge on nil)")
	}
	if *r.Survived {
		t.Fatal("Survived is true for a pre-gate failure: the edge is unusable")
	}
	if r.FailRate != 1 {
		t.Errorf("FailRate = %v, want 1", r.FailRate)
	}
	if r.Fails != r.Probes || r.Probes != 4 {
		t.Errorf("Fails/Probes = %d/%d, want 4/4", r.Fails, r.Probes)
	}
	if r.Err == "" {
		t.Error("Err is empty: the cause should survive into the result")
	}
}
