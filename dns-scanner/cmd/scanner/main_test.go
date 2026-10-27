package main

import (
	"testing"
	"time"

	"mdns-scanner/internal/score"
	"mdns-scanner/internal/targets"
)

func TestFlattenSkipsExcludedAndRecentCertFailures(t *testing.T) {
	const now = 1_000_000
	ttl := 5 * 24 * time.Hour
	ttlSec := int64(ttl / time.Second)
	tiers := []targets.Tier{{Name: "t", IPs: []string{"a", "b", "c", "d", "e"}}}
	exclude := map[string]struct{}{"b": {}}
	certFailed := map[string]int64{
		"c": now - 1,          // recent cert failure → skipped
		"d": now - ttlSec - 1, // expired failure → re-probed
	}
	got := flatten(tiers, exclude, certFailed, now, ttl)
	want := []string{"a", "d", "e"} // b excluded, c suppressed by TTL
	if len(got) != len(want) {
		t.Fatalf("flatten = %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("flatten = %v, want %v", got, want)
		}
	}
}

func TestIntersectCountReloadDecision(t *testing.T) {
	existing := []string{"1.1.1.1", "2.2.2.2", "3.3.3.3"}
	working := []score.Result{{IP: "2.2.2.2"}, {IP: "9.9.9.9"}}
	if got := intersectCount(existing, working); got != 1 {
		t.Errorf("intersectCount = %d, want 1 (only 2.2.2.2 survived)", got)
	}
}
