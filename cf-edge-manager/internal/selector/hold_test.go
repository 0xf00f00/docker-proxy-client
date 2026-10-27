package selector

import "testing"

func cautiousCfg() Config {
	c := mainCfg()
	c.ConfirmTicks = 2
	return c
}

// A blackout makes every edge read dead; nothing may be touched.
func TestHoldTakesNoActionAndSpendsNoProbes(t *testing.T) {
	ap := &fakeApplier{byedpi: "b", snispoof: "s", liveConnect: "s"}
	probes := 0
	why := "blackout"
	s := newSel(t, testCfg(), nil, nil, []string{"b", "s", "x"}, ap)
	s.loss = func(string) float64 { probes++; return 1 }
	s.survival = func(string, bool) *bool { probes++; return bptr(false) }
	s.Use(Hooks{Hold: func() string { return why }})

	for i := range 5 {
		if d := s.Tick(int64(i) * 300); d != 300 {
			t.Fatalf("held delay = %d, want the hold interval (no backoff: recovery must be noticed)", d)
		}
	}
	if probes != 0 || len(ap.wroteB)+len(ap.wroteS) != 0 || ap.restarts != 0 {
		t.Errorf("probes=%d wrote=%v/%v restarts=%d; a held network must cost and change nothing", probes, ap.wroteB, ap.wroteS, ap.restarts)
	}
	if st := s.loadState(); len(st.Quarantine) != 0 || st.Fails != 0 {
		t.Errorf("state %+v: the edge must not be quarantined or backed off for the network's fault", st)
	}
}

func TestABadReadingMustPersistBeforeTheEdgeIsReplaced(t *testing.T) {
	ap := &fakeApplier{snispoof: "cur", liveConnect: "cur"}
	bad := true
	s := newSel(t, cautiousCfg(), nil, nil, []string{"cur", "repl"}, ap)
	s.loss = func(ip string) float64 {
		if ip == "cur" && bad {
			return 0.95
		}
		return 0
	}
	s.Tick(0)
	bad = false
	s.Tick(700) // a good check starts the count over
	bad = true
	s.Tick(1400)
	if len(ap.wroteS) != 0 {
		t.Fatalf("wrote %v; bad readings separated by a good one are not persistence", ap.wroteS)
	}
	s.Tick(2100)
	if ap.snispoof != "repl" {
		t.Errorf("edge = %q; two consecutive bad checks must replace it", ap.snispoof)
	}
}

func TestRotationBudgetStopsAnEdgeSwapLoop(t *testing.T) {
	now := int64(200000)
	cfg := mainCfg()
	cfg.MaxRotations = 2
	ap := &fakeApplier{snispoof: "cur"}
	s := newSel(t, cfg, map[string]float64{"cur": 1}, nil, []string{"cur", "repl"}, ap)
	s.saveState(State{Rotations: map[string][]int64{"snispoof": {now - 3600, now - 7200}}})
	s.Tick(now)
	if len(ap.wroteS) != 0 {
		t.Errorf("wrote %v; two changes in the last day spend the budget", ap.wroteS)
	}
	s.Tick(now + 86400 - 5000) // only the older change has aged out
	if ap.snispoof != "repl" {
		t.Errorf("edge = %q; the budget must free up as changes age out", ap.snispoof)
	}
}
