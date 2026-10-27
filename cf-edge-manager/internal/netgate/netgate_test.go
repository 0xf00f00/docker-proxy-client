package netgate

import (
	"testing"
	"time"
)

func TestClassify(t *testing.T) {
	for _, tc := range []struct {
		name   string
		losses []float64
		want   Regime
	}{
		{"all clean", []float64{0, 0, 0, 0, 0, 0}, Normal},
		{"one edge throttled among clean controls", []float64{0.3, 0, 0, 0, 0, 0, 0, 0}, Normal},
		{"two edges burned, rest fine", []float64{1, 1, 0, 0, 0, 0, 0, 0}, Normal},
		{"nothing reachable", []float64{1, 1, 1, 1, 1, 1}, Blackout},
		{"one lucky edge in a blackout", []float64{1, 1, 1, 1, 1, 0}, Blackout},
		{"a few reachable", []float64{1, 1, 1, 1, 0, 0}, Degraded},
		{"everything lossy but up", []float64{0.3, 0.3, 0.25, 0.3, 0.2, 0.3}, Degraded},
		{"mild global loss is tolerated", []float64{0.1, 0.05, 0.1, 0.12, 0.05, 0.1}, Normal},
		{"too few controls to tell", []float64{1, 1}, Normal},
	} {
		if got := Classify(tc.losses); got != tc.want {
			t.Errorf("%s: %v, want %v", tc.name, got, tc.want)
		}
	}
}

func TestGateHoldsAtOnceButReleasesOnlyAfterSettling(t *testing.T) {
	t0 := time.Date(2026, 10, 27, 0, 0, 0, 0, time.UTC)
	at := func(m int) time.Time { return t0.Add(time.Duration(m) * time.Minute) }
	g := New(2, t0)

	if g.State().Hold() != "" {
		t.Fatal("a fresh gate trusts the network")
	}
	g.Observe(at(5), Blackout)
	if g.State().Hold() != "blackout" {
		t.Fatal("the first bad reading must hold at once")
	}
	g.Observe(at(10), Normal)
	if g.State().Hold() == "" {
		t.Fatal("one good reading must not release the hold")
	}
	g.Observe(at(15), Degraded) // flapped
	g.Observe(at(20), Normal)
	if g.State().Hold() == "" {
		t.Fatal("a flap must restart the settling count")
	}
	from, to := g.Observe(at(25), Normal)
	st := g.State()
	if from == Normal || to != Normal || st.Hold() != "" {
		t.Fatalf("two consecutive good readings must release: %v -> %v", from, to)
	}
	if !st.NormalSince.Equal(at(25)) {
		t.Errorf("NormalSince = %v, want the release time: evidence from the outage is stale", st.NormalSince)
	}
}
