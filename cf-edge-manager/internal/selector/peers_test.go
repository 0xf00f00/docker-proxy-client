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
	s.Use(Hooks{Peers: peersOf([]string{"peer"}, nil)})
	s.Tick(1000)
	if ap.snispoof != "repl" || ap.liveConnect != "repl" {
		t.Errorf("edge = %q (live %q), want 'repl': the peer's edge is off limits", ap.snispoof, ap.liveConnect)
	}
}

func TestPeerSharingAnEdgeIsDiversified(t *testing.T) {
	ap := &fakeApplier{snispoof: "shared"}
	s := newSel(t, mainCfg(), nil, nil, []string{"shared", "other"}, ap)
	s.Use(Hooks{Peers: peersOf([]string{"shared"}, nil)})
	s.Tick(1000)
	if !slices.Equal(ap.wroteS, []string{"other"}) {
		t.Errorf("wrote %v, want [other]: a healthy edge the peer also uses must still move", ap.wroteS)
	}
}
