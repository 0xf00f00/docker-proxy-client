package selector

import (
	"errors"
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

	fakeSNI     string
	wroteSNI    []string
	sniErr      error
	liveConnect string // what the container runs: config as of the last restart
	liveSNI     string
	restartErr  error
}

func (f *fakeApplier) CurrentByedpi() string   { return f.byedpi }
func (f *fakeApplier) CurrentSnispoof() string { return f.snispoof }
func (f *fakeApplier) CurrentFakeSNI() string  { return f.fakeSNI }
func (f *fakeApplier) WriteFakeSNI(sni string) error {
	if f.sniErr != nil {
		return f.sniErr
	}
	f.wroteSNI = append(f.wroteSNI, sni)
	f.fakeSNI = sni
	return nil
}

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

func (f *fakeApplier) RestartSnispoof() error {
	if f.restartErr != nil {
		return f.restartErr
	}
	f.restarts++
	f.liveConnect, f.liveSNI = f.snispoof, f.fakeSNI
	return nil
}

func testCfg() Config {
	return Config{
		KeepMax: 0.20, PickMax: 0.10, MaxCandidates: 5, MaxProbeCandidates: 2,
		BaseInterval: 600, MaxBackoff: 21600, MinRestartGap: 300, QuarantineTTL: 3600,
		SurvivalCheck: true,
		ConfirmTicks:  1, HoldInterval: 300,
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
	survFn := func(ip string, _ bool) *bool {
		if v, ok := surv[ip]; ok {
			return v
		}
		return bptr(true)
	}
	return New(cfg, log, statePath, lossFn, survFn, func() []string { return pool }, ap)
}

func TestPickCleanReturnsSurvivor(t *testing.T) {
	cfg := testCfg()
	pool := []string{"a", "b", "c"}
	surv := map[string]*bool{"a": bptr(false), "b": bptr(true)}
	probes := []string{}
	s := newSel(t, cfg, nil, surv, pool, &fakeApplier{})
	s.survival = func(ip string, _ bool) *bool {
		probes = append(probes, ip)
		if v, ok := surv[ip]; ok {
			return v
		}
		return bptr(true)
	}
	if got := s.pickClean(pool, "", map[string]int64{}, 0, true); got != "b" {
		t.Errorf("got %q, want survivor 'b'", got)
	}
	if len(probes) != 2 || probes[0] != "a" || probes[1] != "b" {
		t.Errorf("probes = %v, want [a b]", probes)
	}
}

func TestPickCleanAllQuarantinedReconsiders(t *testing.T) {
	cfg := testCfg()
	pool := []string{"a", "b"}
	q := map[string]int64{"a": 9999, "b": 9999} // all quarantined in the future
	s := newSel(t, cfg, nil, nil, pool, &fakeApplier{})
	if got := s.pickClean(pool, "", q, 0, true); got == "" {
		t.Error("when every candidate is quarantined, pick must reconsider them all (not starve)")
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

// Regression: a rate-limited rotation was staged in the file and, once the new
// edge probed healthy, never restarted -- the container kept the old edge.
func TestTickStagedRestartGoesLiveLater(t *testing.T) {
	ap := &fakeApplier{byedpi: "good", snispoof: "bad", liveConnect: "bad"}
	s := newSel(t, testCfg(), nil, map[string]*bool{"bad": bptr(false)}, []string{"good", "repl", "bad"}, ap)
	s.saveState(State{LastRestart: 0, Quarantine: map[string]int64{}})
	s.Tick(100) // inside MinRestartGap: staged
	if ap.restarts != 0 || !s.loadState().RestartPending {
		t.Fatalf("expected a staged, pending restart: restarts=%d", ap.restarts)
	}
	s.Tick(700) // "repl" now probes healthy, nothing new to write
	if ap.restarts != 1 || ap.liveConnect != ap.snispoof {
		t.Errorf("staged edge must go live: restarts=%d file=%s live=%s", ap.restarts, ap.snispoof, ap.liveConnect)
	}
	if s.loadState().RestartPending {
		t.Error("pending must clear after the restart")
	}
}

func TestTickFailedRestartRetried(t *testing.T) {
	ap := &fakeApplier{byedpi: "good", snispoof: "bad", liveConnect: "bad", restartErr: errors.New("docker down")}
	s := newSel(t, testCfg(), nil, map[string]*bool{"bad": bptr(false)}, []string{"good", "repl", "bad"}, ap)
	s.Tick(1000)
	if !s.loadState().RestartPending {
		t.Fatal("a failed restart must stay pending")
	}
	ap.restartErr = nil
	s.Tick(1600)
	if ap.restarts != 1 || ap.liveConnect != ap.snispoof {
		t.Errorf("failed restart must be retried: restarts=%d file=%s live=%s", ap.restarts, ap.snispoof, ap.liveConnect)
	}
}
