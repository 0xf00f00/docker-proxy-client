package selector

import (
	"slices"
	"testing"
)

func peersOf(edges, decoys []string) Peers {
	return func() ([]string, []string) { return edges, decoys }
}

func mainCfg() Config {
	cfg := testCfg()
	cfg.NoByedpi = true
	return cfg
}

func TestPeerEdgeIsNeverPicked(t *testing.T) {
	ap := &fakeApplier{snispoof: "bad", liveConnect: "bad"}
	surv := map[string]*bool{"bad": bptr(false)}
	s := newSel(t, mainCfg(), nil, surv, []string{"peer", "repl", "bad"}, ap)
	s.SetPeers(peersOf([]string{"peer"}, nil))
	s.Tick(1000)
	if ap.snispoof != "repl" {
		t.Errorf("rotated onto %q, want 'repl' (the peer's edge is off limits)", ap.snispoof)
	}
	if ap.restarts != 1 || ap.liveConnect != "repl" {
		t.Errorf("rotation must go live: restarts=%d live=%q", ap.restarts, ap.liveConnect)
	}
}

func TestPeerSharingAnEdgeIsDiversified(t *testing.T) {
	ap := &fakeApplier{snispoof: "shared"}
	s := newSel(t, mainCfg(), nil, nil, []string{"shared", "other"}, ap)
	s.SetPeers(peersOf([]string{"shared"}, nil))
	s.Tick(1000)
	if !slices.Equal(ap.wroteS, []string{"other"}) {
		t.Errorf("wrote %v, want [other]: a healthy edge the peer also uses must still move", ap.wroteS)
	}
}

func TestPeerOnlyEdgeLeavesInstanceWhereItIs(t *testing.T) {
	ap := &fakeApplier{snispoof: "mine"}
	s := newSel(t, mainCfg(), nil, nil, []string{"peer"}, ap)
	s.SetPeers(peersOf([]string{"peer"}, nil))
	s.Tick(1000)
	if len(ap.wroteS) != 0 || ap.restarts != 0 {
		t.Errorf("a pool holding only the peer's edge must leave a healthy instance alone: wrote=%v restarts=%d", ap.wroteS, ap.restarts)
	}
}

func TestNoByedpiNeverTouchesByedpi(t *testing.T) {
	ap := &fakeApplier{byedpi: "b", snispoof: "s"}
	loss := map[string]float64{"b": 1}
	s := newSel(t, mainCfg(), loss, nil, []string{"b", "s", "x"}, ap)
	s.Tick(1000)
	if len(ap.wroteB) != 0 {
		t.Errorf("byedpi written %v; an instance without a byedpi path must not manage it", ap.wroteB)
	}
}

func TestNoByedpiOutageBacksOff(t *testing.T) {
	ap := &fakeApplier{byedpi: "h", snispoof: "y"}
	loss := map[string]float64{"y": 1, "p": 1}
	s := newSel(t, mainCfg(), loss, nil, []string{"p"}, ap)
	if d := s.Tick(0); d != s.cfg.BaseInterval<<1 {
		t.Errorf("one path down and no candidate: delay = %d, want %d", d, s.cfg.BaseInterval<<1)
	}
}

func TestPeerDecoyIsNotReused(t *testing.T) {
	d := &dpi{
		plain: map[string]bool{"b.example": true, "c.example": true},
		real:  map[string]bool{"b.example": true, "c.example": true},
	}
	ap := decoyAp()
	s := decoySel(t, testCfg(), d, ap, nil)
	s.SetPeers(peersOf(nil, []string{"b.example"}))
	s.Tick(1000)
	if !slices.Equal(ap.wroteSNI, []string{"c.example"}) {
		t.Errorf("wrote %v, want [c.example]: the peer already uses b.example", ap.wroteSNI)
	}
}
