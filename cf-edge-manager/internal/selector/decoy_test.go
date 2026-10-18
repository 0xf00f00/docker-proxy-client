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

// Regression: the switch restarted at once and stamped LastRestart, so the same
// tick's edge rotation was rate-limited and its connect= never went live.
func TestDecoySwitchAndEdgeRotationBothGoLive(t *testing.T) {
	for _, last := range []int64{0, 950} {
		d := &dpi{
			plain:  map[string]bool{"b.example": true},
			real:   map[string]bool{"b.example": true},
			burned: map[string]bool{"192.0.2.1": true},
		}
		ap := decoyAp()
		s := decoySel(t, testCfg(), d, ap, map[string]*bool{"192.0.2.1": bptr(false)})
		s.saveState(State{LastRestart: last})
		s.Tick(1000)
		if ap.snispoof == "192.0.2.1" || ap.fakeSNI != "b.example" {
			t.Fatalf("last=%d: expected rotation + switch, config connect=%s sni=%s", last, ap.snispoof, ap.fakeSNI)
		}
		if ap.liveConnect != ap.snispoof || ap.liveSNI != ap.fakeSNI {
			t.Errorf("last=%d: stranded config: file %s/%s, live %s/%s", last, ap.snispoof, ap.fakeSNI, ap.liveConnect, ap.liveSNI)
		}
		if ap.restarts != 1 {
			t.Errorf("last=%d: want one restart for both changes, got %d", last, ap.restarts)
		}
	}
}

func TestDecoyNoCandidateThrottles(t *testing.T) {
	d := &dpi{}
	ap := decoyAp()
	s := decoySel(t, testCfg(), d, ap, nil)
	s.Tick(1000)
	if ap.wroteSNI != nil || ap.restarts != 0 {
		t.Fatalf("nothing passes (e.g. a blackout): must not switch or restart: wrote=%v restarts=%d", ap.wroteSNI, ap.restarts)
	}
	n := d.hellos.Load()
	s.Tick(1700)
	if d.hellos.Load() != n {
		t.Error("a fruitless search must be throttled by decoyGap")
	}
	s.Tick(1000 + decoyGap)
	if d.hellos.Load() == n {
		t.Error("search must resume after decoyGap")
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

func TestDecoyNeedsRealPathProbe(t *testing.T) {
	cfg := testCfg()
	cfg.SurvivalCheck = false
	d := &dpi{}
	decoySel(t, cfg, d, decoyAp(), nil).Tick(1000)
	if d.hellos.Load() != 0 {
		t.Error("without the real-path oracle the decoy must be left alone")
	}
}

func TestDecoyNilOpsInert(t *testing.T) {
	s := newSel(t, testCfg(), nil, nil, []string{"192.0.2.1"}, decoyAp())
	s.Tick(1000) // no SetDecoy: must not panic
}

func TestScreenDecoysIncludesCurrent(t *testing.T) {
	d := &dpi{plain: map[string]bool{"old.example": true, "c.example": true}}
	got := decoySel(t, testCfg(), d, decoyAp(), nil).ScreenDecoys(context.Background())
	if got["current"] != "old.example" || !slices.Equal(got["ranked"].([]string), []string{"old.example", "c.example"}) {
		t.Errorf("screen = %v", got)
	}
	if !slices.Equal(got["edges"].([]string), []string{"192.0.2.1", "192.0.2.2"}) {
		t.Errorf("edges = %v", got["edges"])
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

func TestDecoyDeadSparesByedpi(t *testing.T) {
	d := &dpi{plain: map[string]bool{"b.example": true}} // b passes plain TLS but nothing survives the real path
	ap := decoyAp()
	s := decoySel(t, testCfg(), d, ap, allFail())
	s.Tick(1000)
	if got := s.loadState().DecoyDead; got != "old.example" {
		t.Fatalf("DecoyDead = %q", got)
	}
	s.Tick(1700) // decoy check throttled; the verdict must persist
	if ap.wroteB != nil {
		t.Errorf("byedpi doesn't carry the decoy; dead-decoy verdicts must not move it: %v", ap.wroteB)
	}
	if ap.wroteS == nil {
		t.Error("snispoof keeps rotating (it varies the edges the next search tries)")
	}
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

func TestDecoyDeadAppliesOnlyToLiveDecoy(t *testing.T) {
	ap := decoyAp()
	s := decoySel(t, testCfg(), &dpi{}, ap, allFail())
	s.saveState(State{DecoyDead: "gone.example", DecoyCheckAfter: 1 << 40})
	s.Tick(1000)
	if ap.wroteB == nil {
		t.Error("a verdict on a decoy no longer in the config must not gate byedpi")
	}
}

func TestDecoyDeadClearsWhenDecoyPasses(t *testing.T) {
	s := decoySel(t, testCfg(), &dpi{plain: map[string]bool{"old.example": true}}, decoyAp(), nil)
	s.saveState(State{DecoyDead: "old.example"})
	s.Tick(1000)
	if got := s.loadState().DecoyDead; got != "" {
		t.Errorf("DecoyDead = %q after the decoy passed", got)
	}
}
