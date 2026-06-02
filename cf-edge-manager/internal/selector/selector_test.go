package selector

import (
	"io"
	"log/slog"
	"path/filepath"
	"testing"
)

func bptr(b bool) *bool { return &b }

type fakeApplier struct {
	byedpi, snispoof string
	wroteB, wroteS   []string
	restarts         int
}

func (f *fakeApplier) CurrentByedpi() string   { return f.byedpi }
func (f *fakeApplier) CurrentSnispoof() string { return f.snispoof }
func (f *fakeApplier) WriteByedpi(ip string) error {
	f.wroteB = append(f.wroteB, ip)
	f.byedpi = ip
	return nil
}

func (f *fakeApplier) WriteSnispoof(ip string) error {
	f.wroteS = append(f.wroteS, ip)
	f.snispoof = ip
	return nil
}
func (f *fakeApplier) RestartSnispoof() error { f.restarts++; return nil }

func testCfg() Config {
	return Config{
		KeepMax: 0.20, PickMax: 0.10, MaxCandidates: 5, MaxProbeCandidates: 2,
		BaseInterval: 600, MaxBackoff: 21600, MinRestartGap: 300, QuarantineTTL: 3600,
		SurvivalCheck: true,
	}
}

func newSel(t *testing.T, cfg Config, loss map[string]float64, surv map[string]*bool, pool []string, ap Applier) *Selector {
	t.Helper()
	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	statePath := filepath.Join(t.TempDir(), "state.json")
	lossFn := func(ip string) float64 {
		if v, ok := loss[ip]; ok {
			return v
		}
		return 0.0
	}
	survFn := func(ip string) *bool {
		if v, ok := surv[ip]; ok {
			return v
		}
		return bptr(true)
	}
	return New(cfg, log, statePath, lossFn, survFn, func() []string { return pool }, ap)
}

func TestEdgeOK(t *testing.T) {
	cfg := testCfg()
	probed := 0
	loss := map[string]float64{"dead": 0.9, "clean": 0.0}
	surv := map[string]*bool{"failbad": bptr(false), "nosig": nil}
	s := newSel(t, cfg, loss, surv, nil, &fakeApplier{})
	s.survival = func(ip string) *bool {
		probed++
		if v, ok := surv[ip]; ok {
			return v
		}
		return bptr(true)
	}

	if s.edgeOK("dead", cfg.KeepMax, "t", true) {
		t.Error("dead edge must be rejected")
	}
	if probed != 0 {
		t.Error("prober must not run on a tcp_loss-dead edge")
	}
	if !s.edgeOK("clean", cfg.KeepMax, "t", true) {
		t.Error("clean+survived edge must be ok")
	}
	if s.edgeOK("failbad", cfg.KeepMax, "t", true) {
		t.Error("clean+real-path-failed must be rejected (loss-blind fix)")
	}
	if !s.edgeOK("nosig", cfg.KeepMax, "t", true) {
		t.Error("clean+no-signal must be kept (never blame edge)")
	}
	// kill-switch: survival disabled -> loss-only
	cfg2 := cfg
	cfg2.SurvivalCheck = false
	s2 := newSel(t, cfg2, loss, surv, nil, &fakeApplier{})
	called := false
	s2.survival = func(string) *bool { called = true; return bptr(false) }
	if !s2.edgeOK("clean", cfg2.KeepMax, "t", true) || called {
		t.Error("with SurvivalCheck off, accept on tcp_loss and never probe")
	}
}

func TestPickCleanBudgetAndFallback(t *testing.T) {
	cfg := testCfg() // MaxProbeCandidates=2, MaxCandidates=5
	pool := []string{"a", "b", "c", "d", "e"}
	// all tcp_loss-clean; all real-path FAIL -> 2 probed (a,b), then first unprobed-clean (c) is fallback
	surv := map[string]*bool{"a": bptr(false), "b": bptr(false), "c": bptr(false), "d": bptr(false), "e": bptr(false)}
	probes := []string{}
	s := newSel(t, cfg, nil, surv, pool, &fakeApplier{})
	s.survival = func(ip string) *bool { probes = append(probes, ip); return surv[ip] }
	got := s.pickClean(pool, "", map[string]int64{}, 0)
	if len(probes) != 2 {
		t.Errorf("probe budget = %d, want 2", len(probes))
	}
	if got != "c" {
		t.Errorf("fallback = %q, want first unprobed-clean 'c'", got)
	}
}

func TestPickCleanReturnsSurvivor(t *testing.T) {
	cfg := testCfg()
	pool := []string{"a", "b", "c"}
	surv := map[string]*bool{"a": bptr(false), "b": bptr(true)}
	probes := []string{}
	s := newSel(t, cfg, nil, surv, pool, &fakeApplier{})
	s.survival = func(ip string) *bool {
		probes = append(probes, ip)
		if v, ok := surv[ip]; ok {
			return v
		}
		return bptr(true)
	}
	if got := s.pickClean(pool, "", map[string]int64{}, 0); got != "b" {
		t.Errorf("got %q, want survivor 'b'", got)
	}
	if len(probes) != 2 || probes[0] != "a" || probes[1] != "b" {
		t.Errorf("probes = %v, want [a b]", probes)
	}
}

func TestPickCleanExcludeAndTcpLoss(t *testing.T) {
	cfg := testCfg()
	pool := []string{"x", "y", "z"}
	loss := map[string]float64{"y": 0.5} // y is tcp-dead
	s := newSel(t, cfg, loss, nil, pool, &fakeApplier{})
	// exclude x; y rejected on tcp_loss; z clean+survived
	if got := s.pickClean(pool, "x", map[string]int64{}, 0); got != "z" {
		t.Errorf("got %q, want 'z' (x excluded, y tcp-dead)", got)
	}
}

func TestPickCleanAllQuarantinedReconsiders(t *testing.T) {
	cfg := testCfg()
	pool := []string{"a", "b"}
	q := map[string]int64{"a": 9999, "b": 9999} // all quarantined in the future
	s := newSel(t, cfg, nil, nil, pool, &fakeApplier{})
	if got := s.pickClean(pool, "", q, 0); got == "" {
		t.Error("when every candidate is quarantined, pick must reconsider them all (not starve)")
	}
}

func TestTickGatesOnNextRun(t *testing.T) {
	s := newSel(t, testCfg(), nil, nil, []string{"a"}, &fakeApplier{})
	s.saveState(State{NextRun: 1000, Quarantine: map[string]int64{}})
	if d := s.Tick(400); d != 600 {
		t.Errorf("Tick before next_run should return remainder 600, got %d", d)
	}
}

func TestTickEmptyPool(t *testing.T) {
	s := newSel(t, testCfg(), nil, nil, nil, &fakeApplier{})
	if d := s.Tick(0); d != 600 {
		t.Errorf("empty pool should return BaseInterval 600, got %d", d)
	}
}

func TestTickHealthyKeepsAndResetsFails(t *testing.T) {
	cfg := testCfg()
	ap := &fakeApplier{byedpi: "a", snispoof: "b"} // both in-use, both healthy (default loss 0, surv true)
	s := newSel(t, cfg, nil, nil, []string{"a", "b", "c"}, ap)
	s.saveState(State{Fails: 3, Quarantine: map[string]int64{}})
	d := s.Tick(0)
	if d != cfg.BaseInterval {
		t.Errorf("healthy delay = %d, want %d", d, cfg.BaseInterval)
	}
	if len(ap.wroteB) != 0 || len(ap.wroteS) != 0 || ap.restarts != 0 {
		t.Error("healthy edges must not be rewritten/restarted")
	}
	if st := s.loadState(); st.Fails != 0 {
		t.Errorf("fails should reset to 0 on healthy, got %d", st.Fails)
	}
}

func TestTickRotatesDegradedInUseEdge(t *testing.T) {
	cfg := testCfg()
	// in-use snispoof "bad" is tcp-clean but real-path FAILS -> must rotate (the core fix)
	ap := &fakeApplier{byedpi: "good1", snispoof: "bad"}
	surv := map[string]*bool{"bad": bptr(false)}
	pool := []string{"good1", "repl", "bad"}
	s := newSel(t, cfg, nil, surv, pool, ap)
	s.Tick(0)
	if len(ap.wroteS) == 0 {
		t.Fatal("degraded loss-blind snispoof edge must be rotated (rewritten)")
	}
	if ap.snispoof == "bad" {
		t.Errorf("snispoof still on bad edge after rotate; now=%q", ap.snispoof)
	}
	st := s.loadState()
	if _, ok := st.Quarantine["bad"]; !ok {
		t.Error("rotated-off edge must be quarantined")
	}
}

func TestTickBackoffOnOutage(t *testing.T) {
	cfg := testCfg()
	// both in-use edges tcp-dead AND no clean candidates -> 0 healthy -> backoff
	ap := &fakeApplier{byedpi: "x", snispoof: "y"}
	loss := map[string]float64{"x": 1, "y": 1, "p": 1, "q": 1}
	s := newSel(t, cfg, loss, nil, []string{"p", "q"}, ap)
	s.saveState(State{Fails: 0, Quarantine: map[string]int64{}})
	d := s.Tick(0)
	if d != cfg.BaseInterval<<1 { // fails becomes 1 -> 600<<1 = 1200
		t.Errorf("first outage backoff = %d, want %d", d, cfg.BaseInterval<<1)
	}
}

func TestTickRestartRateLimit(t *testing.T) {
	cfg := testCfg()
	ap := &fakeApplier{byedpi: "good", snispoof: "bad"}
	surv := map[string]*bool{"bad": bptr(false)}
	s := newSel(t, cfg, nil, surv, []string{"good", "repl", "bad"}, ap)
	// last restart was recent (now - lastRestart < MinRestartGap) -> write but DON'T restart
	s.saveState(State{LastRestart: 0, Quarantine: map[string]int64{}})
	s.Tick(100) // 100 - 0 = 100 < 300 gap
	if ap.restarts != 0 {
		t.Errorf("restart should be rate-limited (gap not elapsed), got %d restarts", ap.restarts)
	}
	if len(ap.wroteS) == 0 {
		t.Error("config should still be written even when restart is rate-limited")
	}
}
