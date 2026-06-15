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
	got := flatten(tiers, exclude, certFailed, now, ttl, 0)
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

func TestFlattenHonorsMaxProbes(t *testing.T) {
	tiers := []targets.Tier{{Name: "t", IPs: []string{"a", "b", "c", "d"}}}
	if got := flatten(tiers, nil, nil, 0, time.Hour, 2); len(got) != 2 {
		t.Errorf("flatten(maxProbes=2) len = %d, want 2 (%v)", len(got), got)
	}
}

func TestIntersectCountReloadDecision(t *testing.T) {
	existing := []string{"1.1.1.1", "2.2.2.2", "3.3.3.3"}
	working := []score.Result{{IP: "2.2.2.2"}, {IP: "9.9.9.9"}}
	if got := intersectCount(existing, working); got != 1 {
		t.Errorf("intersectCount = %d, want 1 (only 2.2.2.2 survived)", got)
	}
}

func TestJitteredWithinTenPercent(t *testing.T) {
	base := time.Hour
	lo, hi := base-base/10, base+base/5 // upper bound is +20% slack from the rand range
	for i := 0; i < 1000; i++ {
		d := jittered(base)
		if d < lo || d > hi {
			t.Fatalf("jittered(%v) = %v, outside [%v, %v]", base, d, lo, hi)
		}
	}
	if jittered(0) != 0 {
		t.Errorf("jittered(0) = %v, want 0", jittered(0))
	}
}

func TestCertReportSummary(t *testing.T) {
	if got := newCertReport().summary(); got != "" {
		t.Errorf("empty summary = %q, want \"\"", got)
	}
	r := newCertReport()
	r.note(true, "")                  // accepted — counts as an attempt, no reason
	r.note(false, "exec: dial error") // collapses to "exec"
	r.note(false, "exec: timeout")
	r.note(false, "") // empty reason → "rejected"
	r.addSweepFail("9.9.9.9")
	got := r.summary()
	want := "certs: 4 tried — exec=2 rejected=1"
	if got != want {
		t.Errorf("summary = %q, want %q", got, want)
	}
	if fails := r.sweepFails(); len(fails) != 1 || fails[0] != "9.9.9.9" {
		t.Errorf("sweepFails = %v, want [9.9.9.9]", fails)
	}
}

func TestFunnelAddTallies(t *testing.T) {
	f := &funnel{}
	f.add(score.Result{})                                                                                                      // dead: probed only
	f.add(score.Result{AliveRTT: time.Millisecond})                                                                            // alive
	f.add(score.Result{AliveRTT: time.Millisecond, NXOK: true})                                                                // +nx
	f.add(score.Result{AliveRTT: time.Millisecond, NXOK: true, Forwards: true, EDNSMax: 0, UploadOK: true})                    // near-miss (no edns → no gates)
	f.add(score.Result{AliveRTT: time.Millisecond, NXOK: true, Forwards: true, EDNSMax: 512, UploadOK: true, Certified: true}) // full pass
	g := f.snapshot()
	if g.Probed != 5 || g.Alive != 4 || g.NX != 3 || g.Forward != 2 || g.EDNS != 1 || g.Upload != 2 || g.Gates != 1 || g.Cert != 1 {
		t.Errorf("funnel snapshot = %+v", g)
	}
}

func TestNearMissCollectsAndExcludesWorking(t *testing.T) {
	l := &liveSet{}
	nm := newNearMiss(2, l)
	// Forwards but failed gates (EDNSMax 0) → near-miss.
	nm.consider(score.Result{IP: "1.1.1.1", Forwards: true, UploadOK: true})
	nm.consider(score.Result{IP: "1.1.1.1", Forwards: true, UploadOK: true}) // dup ignored
	nm.consider(score.Result{IP: "8.8.8.8", Forwards: true, UploadOK: true})
	nm.consider(score.Result{IP: "9.9.9.9", Forwards: true, UploadOK: true}) // over cap (2) → dropped
	// Not a near-miss: no Forwards, or already certified.
	nm.consider(score.Result{IP: "2.2.2.2", Forwards: false})
	nm.consider(score.Result{IP: "3.3.3.3", Forwards: true, NXOK: true, EDNSMax: 512, UploadOK: true, Certified: true})
	got := nm.ips()
	if len(got) != 2 || got[0] != "1.1.1.1" || got[1] != "8.8.8.8" {
		t.Fatalf("nearMiss ips = %v, want [1.1.1.1 8.8.8.8]", got)
	}
	// Once 1.1.1.1 is working, it is excluded from the backup candidate list.
	l.working = []score.Result{{IP: "1.1.1.1"}}
	if got := nm.ips(); len(got) != 1 || got[0] != "8.8.8.8" {
		t.Errorf("nearMiss ips after working = %v, want [8.8.8.8]", got)
	}
}
