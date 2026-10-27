package selector

import "testing"

func withAck(s *Selector, a Ack) *Selector {
	s.Use(Hooks{Ack: func() Ack { return a }})
	return s
}

// The case that went unnoticed: every probe passes, but real connections fail.
func TestAckBadRotatesAnEdgeThatPassesItsProbes(t *testing.T) {
	ap := &fakeApplier{snispoof: "slow", liveConnect: "slow"}
	withAck(newSel(t, mainCfg(), nil, nil, []string{"slow", "repl"}, ap), Ack{Ratio: 0.4, Samples: 200, Bad: true}).Tick(1000)
	if ap.snispoof != "repl" || ap.liveConnect != "repl" {
		t.Errorf("edge = %q (live %q), want rotated to 'repl'", ap.snispoof, ap.liveConnect)
	}
}

func TestAckSystemicDoesNotBlameTheEdge(t *testing.T) {
	ap := &fakeApplier{snispoof: "mine"}
	withAck(newSel(t, mainCfg(), nil, nil, []string{"mine", "other"}, ap), Ack{Ratio: 0.3, Samples: 200, Bad: true, Systemic: true}).Tick(1000)
	if len(ap.wroteS) != 0 {
		t.Errorf("wrote %v: when every instance fails alike rotating one edge fixes nothing", ap.wroteS)
	}
}

// Right after a rotation is staged the log still describes the previous edge.
func TestAckIgnoredWhileARestartIsPending(t *testing.T) {
	ap := &fakeApplier{snispoof: "new"}
	s := withAck(newSel(t, mainCfg(), nil, nil, []string{"new", "other"}, ap), Ack{Ratio: 0.3, Samples: 200, Bad: true})
	s.saveState(State{RestartPending: true, LastRestart: 990})
	s.Tick(1000)
	if len(ap.wroteS) != 0 {
		t.Errorf("wrote %v: stale evidence about the old edge must not condemn the new one", ap.wroteS)
	}
}

// Keeping an in-use edge tolerates some failed probes; adopting a candidate does not.
func TestSurvivalIsStrictOnlyForCandidates(t *testing.T) {
	var asked []bool
	ap := &fakeApplier{snispoof: "bad"}
	s := newSel(t, mainCfg(), nil, nil, []string{"bad", "repl"}, ap)
	s.survival = func(ip string, strict bool) *bool {
		asked = append(asked, strict)
		return bptr(ip != "bad")
	}
	s.Tick(1000)
	if len(asked) != 2 || asked[0] || !asked[1] {
		t.Errorf("strict flags = %v, want [false (keep check) true (candidate)]", asked)
	}
}
