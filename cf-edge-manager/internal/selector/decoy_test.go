package selector

import (
	"context"
	"errors"
	"slices"
	"sync/atomic"
	"testing"
)

// dpi models the network: plain = names whose plain TLS hello completes, real =
// decoys that survive the real path, burned = edges that fail whatever the decoy.
type dpi struct {
	plain, real, burned map[string]bool
	hellos              atomic.Int32
	trials              []string // decoys trialled, in order
}

func (d *dpi) hello(_ context.Context, _, sni string) bool {
	d.hellos.Add(1)
	return d.plain[sni]
}

func (d *dpi) trial(ip, sni string) *bool {
	d.trials = append(d.trials, sni)
	v := d.real[sni] && !d.burned[ip]
	return &v
}

var (
	decoyPool  = []string{"192.0.2.1", "192.0.2.2", "192.0.2.3", "192.0.2.4"}
	decoyCands = []string{"a.example", "b.example", "c.example", "d.example"}
)

// decoyAp: snispoof on 192.0.2.1 with decoy old.example, both live.
func decoyAp() *fakeApplier {
	return &fakeApplier{
		byedpi: "198.51.100.9", snispoof: "192.0.2.1", fakeSNI: "old.example",
		liveConnect: "192.0.2.1", liveSNI: "old.example",
	}
}

func decoySel(t *testing.T, cfg Config, d *dpi, ap *fakeApplier, surv map[string]*bool) *Selector {
	t.Helper()
	s := newSel(t, cfg, nil, surv, decoyPool, ap)
	s.SetDecoy(d.hello, d.trial, decoyCands)
	return s
}

func TestDecoyHealthyCostsOnlyAHello(t *testing.T) {
	d := &dpi{plain: map[string]bool{"old.example": true}}
	ap := decoyAp()
	s := decoySel(t, testCfg(), d, ap, nil)
	s.Tick(1000)
	if d.trials != nil || ap.wroteSNI != nil || ap.restarts != 0 {
		t.Errorf("healthy decoy: trials=%v wrote=%v restarts=%d", d.trials, ap.wroteSNI, ap.restarts)
	}
	if n := d.hellos.Load(); n == 0 || n > decoyScreenEdges {
		t.Errorf("hellos = %d, want 1..%d", n, decoyScreenEdges)
	}
	if st := s.loadState(); st.DecoyCheckAfter != 0 {
		t.Errorf("a passing hello must not pause checks: %+v", st)
	}
}

// A name the edge doesn't serve misses plain TLS yet can work as a decoy.
func TestDecoyPlainMissButRealPathOKKeeps(t *testing.T) {
	d := &dpi{real: map[string]bool{"old.example": true}}
	ap := decoyAp()
	s := decoySel(t, testCfg(), d, ap, nil)
	s.Tick(1000)
	if ap.wroteSNI != nil {
		t.Fatalf("must not replace a decoy that works on the real path: %v", ap.wroteSNI)
	}
	n := d.hellos.Load()
	s.Tick(1700) // due tick, inside decoyGap
	if d.hellos.Load() != n {
		t.Error("the confirmed decoy must not be re-checked inside decoyGap")
	}
	s.Tick(1000 + decoyGap)
	if d.hellos.Load() == n {
		t.Error("checks must resume after decoyGap")
	}
}

func TestDecoyBurnedSwitchesAndGoesLive(t *testing.T) {
	for _, last := range []int64{0, 950} { // 950: inside MinRestartGap
		d := &dpi{plain: map[string]bool{"b.example": true, "c.example": true}, real: map[string]bool{"b.example": true}}
		ap := decoyAp()
		s := decoySel(t, testCfg(), d, ap, nil)
		s.saveState(State{LastRestart: last})
		s.Tick(1000)
		if !slices.Equal(ap.wroteSNI, []string{"b.example"}) {
			t.Fatalf("last=%d: wrote %v, want [b.example] (trials %v)", last, ap.wroteSNI, d.trials)
		}
		if ap.restarts != 1 || ap.liveSNI != "b.example" {
			t.Errorf("last=%d: new decoy must go live with one restart, even inside MinRestartGap: restarts=%d live=%q", last, ap.restarts, ap.liveSNI)
		}
		if slices.Contains(d.trials, "a.example") {
			t.Errorf("last=%d: a plain-TLS reject must not cost a real-path trial: %v", last, d.trials)
		}
		if st := s.loadState(); st.LastRestart != 1000 || st.DecoyCheckAfter != 1000+decoyGap {
			t.Errorf("last=%d: state %+v", last, st)
		}
	}
}

func TestDecoyTrialBudget(t *testing.T) {
	d := &dpi{
		plain: map[string]bool{"a.example": true, "b.example": true, "c.example": true, "d.example": true},
		real:  map[string]bool{"d.example": true}, // 4th in rank, beyond decoyMaxTrials
	}
	ap := decoyAp()
	decoySel(t, testCfg(), d, ap, nil).Tick(1000)
	if ap.wroteSNI != nil || slices.Contains(d.trials, "d.example") {
		t.Errorf("decoyMaxTrials must cap the search: wrote=%v trials=%v", ap.wroteSNI, d.trials)
	}
}

func TestDecoyWriteErrorNoRestart(t *testing.T) {
	d := &dpi{plain: map[string]bool{"b.example": true}, real: map[string]bool{"b.example": true}}
	ap := decoyAp()
	ap.sniErr = errors.New("read-only")
	s := decoySel(t, testCfg(), d, ap, nil)
	s.Tick(1000)
	if ap.restarts != 0 || ap.fakeSNI != "old.example" {
		t.Errorf("failed write must not restart: restarts=%d sni=%q", ap.restarts, ap.fakeSNI)
	}
	if st := s.loadState(); st.DecoyCheckAfter != 1000+decoyGap {
		t.Errorf("failed write must throttle the next search: %+v", st)
	}
}

// every edge fails the real path while the decoy is dead
func allFail() map[string]*bool {
	m := map[string]*bool{"198.51.100.9": bptr(false)}
	for _, ip := range decoyPool {
		m[ip] = bptr(false)
	}
	return m
}

func TestDecoyBlackoutIsNotDead(t *testing.T) {
	d := &dpi{} // no name passes plain TLS: can't blame the decoy
	ap := decoyAp()
	s := decoySel(t, testCfg(), d, ap, allFail())
	s.Tick(1000)
	if s.loadState().DecoyDead != "" || ap.wroteB == nil {
		t.Errorf("blackout: DecoyDead=%q byedpi writes=%v", s.loadState().DecoyDead, ap.wroteB)
	}
}
