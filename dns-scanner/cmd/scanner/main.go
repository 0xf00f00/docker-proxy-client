package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"math/rand"
	"net"
	"os"
	"os/signal"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"mdns-scanner/internal/api"
	"mdns-scanner/internal/assign"
	"mdns-scanner/internal/certify"
	"mdns-scanner/internal/pacer"
	"mdns-scanner/internal/scan"
	"mdns-scanner/internal/score"
	"mdns-scanner/internal/state"
	"mdns-scanner/internal/targets"
)

const (
	dayUnit       time.Duration = 24 * time.Hour // a backoff "day"
	deepWorkers                 = 8              // funnel concurrency; I/O-bound, pacer QPS is the real throttle
	certifyConc                 = 2              // engine certification is heavy — keep it low
	lossSamples                 = 5              // quick repeats for the loss/jitter probe
	cidrSampleMax               = 5000           // cap on IPs the CIDR tier contributes
	historyRetry                = 30             // recent history IPs retried first each cycle
	historyCap                  = 200            // resolver history pool size
	maxProbes                   = 0              // 0 = unbounded sweep

	canaryAnchors                = 5                  // history IPs the outage-recovery canary probes
	canaryInterval time.Duration = time.Hour          // recovery-canary poll cadence while in outage
	certFailTTL    time.Duration = 5 * 24 * time.Hour // how long a cert failure suppresses a sweep re-probe
	certFailCap                  = 5000               // max cert-failure entries kept (oldest evicted)
)

type config struct {
	domain       string
	probeDomain  string
	key          string
	method       int
	baseEncode   bool
	probeBin     string
	clientConfig string
	minUp        int
	maxUp        int
	minDown      int
	maxDown      int

	bindIP        string
	qps           int
	cooldown      time.Duration
	targetN       int
	watermark     int
	flushCooldown time.Duration // min gap between mid-cycle mdns restarts while starving
	dataDir       string
	stageTimeout  time.Duration

	daemon        bool
	baseDays      int
	maxDays       int
	resolversPath string
	statePath     string
	reloadURL     string
	apiAddr       string
	apiSecret     string
}

func main() {
	setupLogging()
	cfg := loadConfig()
	ctx := signalContext()

	p := pacer.New(cfg.qps, cfg.cooldown)
	defer p.Close()
	deps := scan.Deps{
		Prober:       buildProber(cfg),
		Pacer:        p,
		BindIP:       cfg.bindIP,
		ProbeDomain:  cfg.probeDomain,
		StageTimeout: cfg.stageTimeout,
		LossSamples:  lossSamples,
		Workers:      deepWorkers,
		CertifyConc:  certifyConc,
	}

	// Ad-hoc mode: scan exactly the given IPs once, no assignment/daemon.
	if args := os.Args[1:]; len(args) > 0 {
		deps.OnAccept = func(r score.Result, n int) {
			fmt.Printf("  [+] %-15s accepted (%d/%d)\n", r.IP, n, cfg.targetN)
		}
		res, _ := scan.Run(ctx, deps, args, cfg.targetN, true)
		report(cfg, deps.Prober != nil, score.RankWorking(res))
		return
	}

	srv := api.NewServer(cfg.apiSecret)
	srv.SetTarget(cfg.targetN) // surface "accepted of target" progress to the dashboard
	// Pause/resume the worker pool through the control API without losing progress.
	deps.Gate = srv.Wait
	if cfg.daemon && cfg.apiAddr != "" {
		if !srv.AuthEnabled() {
			slog.Warn("control API is UNAUTHENTICATED — set SCANNER_API_SECRET (dev only)")
		}
		go func() {
			slog.Info("control API listening", "addr", cfg.apiAddr)
			if err := srv.Run(ctx, cfg.apiAddr); err != nil {
				slog.Error("api server exited", "err", err)
			}
		}()
	}

	st, err := state.Load(cfg.statePath)
	if err != nil {
		slog.Error("state load failed", "err", err)
	}
	if st.BackoffDays < cfg.baseDays {
		st.BackoffDays = cfg.baseDays
	}

	for {
		if cfg.daemon {
			interval := time.Duration(st.BackoffDays) * dayUnit
			next := time.Now().Add(interval) // never-run: bootstrap a full interval out
			if st.UpdatedUnix > 0 {
				next = time.Unix(st.UpdatedUnix, 0).Add(interval)
			}
			srv.SetSchedule(next.Unix(), st.BackoffDays)
			slog.Info("scan scheduled", "interval_days", st.BackoffDays, "next", next.Format(time.RFC3339))
			inOutage := len(st.Working) == 0
			anchors := st.RecentHistoryIPs(canaryAnchors, nil)
			if !waitNext(ctx, cfg, deps, srv, anchors, time.Until(next), inOutage) {
				return
			}
		}

		runCycle(ctx, cfg, deps, srv, &st)
		if !cfg.daemon {
			return
		}
	}
}

func waitNext(ctx context.Context, cfg config, deps scan.Deps, srv *api.Server, anchors []string, interval time.Duration, inOutage bool) bool {
	if !inOutage || len(anchors) == 0 {
		select {
		case <-ctx.Done():
			return false
		case <-time.After(interval):
			return true
		case <-srv.Trigger():
			slog.Info("manual scan triggered")
			return true
		}
	}
	deadline := time.Now().Add(interval)
	for {
		wait := jittered(canaryInterval)
		if rem := time.Until(deadline); wait > rem {
			wait = rem
		}
		if wait <= 0 {
			return true // backoff elapsed — run the scheduled cycle
		}
		select {
		case <-ctx.Done():
			return false
		case <-srv.Trigger():
			slog.Info("manual scan triggered")
			return true
		case <-time.After(wait):
		}
		if scan.Anchors(ctx, deps, anchors, cfg.probeDomain) {
			slog.Info("recovery canary tripped — intl recursion reachable, scanning early", "anchors", len(anchors))
			return true
		}
	}
}

// jittered returns d ± up to 10%, so polls don't align to a fixed wall-clock beat.
func jittered(d time.Duration) time.Duration {
	if d <= 0 {
		return d
	}
	return d - d/10 + time.Duration(rand.Int63n(int64(d/5)+1))
}

func runCycle(ctx context.Context, cfg config, base scan.Deps, srv *api.Server, st *state.State) {
	// Scan-scoped context so the control API can stop just this run (via the
	// registered cancel) without tearing down the daemon.
	scanCtx, cancel := context.WithCancel(ctx)
	defer cancel()

	start := time.Now()
	srv.BeginRun(start.Unix(), cancel)
	slog.Info("scan cycle started", "time", start.Format(time.RFC3339))

	prog := newProgress(srv)
	existing := assign.ReadAll(cfg.resolversPath)
	l := &liveSet{
		cfg:         cfg,
		srv:         srv,
		history:     st.History,
		backoffDays: st.BackoffDays,
		existing:    existing,
		certifyOn:   base.Prober != nil,
		certFailed:  st.CertFailed,
	}
	rep := newCertReport()
	deps := base
	deps.OnAccept = l.onAccept
	deps.OnProbe = prog.tick

	// Verify leg certifies known-good IPs even when cheap gates flake; sweep leg
	// keeps the full funnel and feeds its cert failures to the skip-list.
	verifyDeps := deps
	verifyDeps.CertifyAnyway = true
	verifyDeps.OnCertResult = func(_ string, accepted bool, reason string) { rep.note(accepted, reason) }

	sweepDeps := deps
	sweepDeps.OnCertResult = func(ip string, accepted bool, reason string) {
		rep.note(accepted, reason)
		if !accepted {
			rep.addSweepFail(ip)
		}
	}

	// 1) Re-verify what's already in play
	curIPs := dedup(append(append([]string{}, st.IPs()...), existing...))
	retry := dedup(append(append([]string{}, curIPs...), st.RecentHistoryIPs(historyRetry, setOf(curIPs))...))
	if len(retry) > 0 {
		prog.beginPhase("verify", len(retry))
		scan.Run(scanCtx, verifyDeps, retry, len(retry), false)
		prog.flush()
	}

	// 2) Sweep the priority tiers only if still short of target.
	if l.count() < cfg.targetN {
		seed := time.Now().UnixNano()
		if tiers, err := targets.Load(cfg.dataDir, seed, cidrSampleMax); err != nil {
			slog.Error("load targets failed", "err", err)
		} else {
			need := cfg.targetN - l.count()
			sweep := flatten(tiers, l.ipsSet(), st.CertFailed, start.Unix(), certFailTTL, maxProbes)
			prog.beginPhase("sweep", len(sweep))
			slog.Info("sweeping tiers", "need", need, "candidates", len(sweep), "seed", seed)
			scan.Run(scanCtx, sweepDeps, sweep, need, true)
			prog.flush()
		}
	} else {
		slog.Info("known resolvers satisfy target — no sweep", "count", l.count())
	}

	working := score.RankWorking(l.snapshot())

	if scanCtx.Err() != nil {
		slog.Info("scan cancelled — keeping current resolvers (no clobber, no backoff change)", "probed", prog.total())
		srv.EndRun(start.Unix(), int(time.Since(start).Seconds()), "stopped", toResolverInfo(working), len(st.History))
		report(cfg, base.Prober != nil, working)
		return
	}

	// 3) Final assign
	if !l.certifyOn {
		slog.Warn("certification disabled — NOT assigning; live resolvers left untouched",
			"gate_survivors", len(working), "current", len(existing))
	} else if changed, err := assign.WriteManaged(cfg.resolversPath, ipsOf(working)); err != nil {
		slog.Error("assign failed", "err", err)
	} else if changed {
		survivors := intersectCount(existing, working)
		if survivors <= cfg.watermark {
			slog.Info("client_resolvers.txt updated — reloading mdns (live survivors at/below watermark)",
				"resolvers", len(working), "live_survivors", survivors, "watermark", cfg.watermark)
			reload(cfg.reloadURL)
		} else {
			slog.Info("client_resolvers.txt updated — reload deferred (healthy: live survivors above watermark)",
				"resolvers", len(working), "live_survivors", survivors, "watermark", cfg.watermark)
		}
	} else if len(working) == 0 {
		slog.Warn("0 working found — keeping previous resolvers (no clobber)", "previous", len(existing))
	} else {
		slog.Info("working set unchanged — no reload")
	}

	// 4) Update history, backoff, cert-failure skip-list, persist.
	st.History = mergeHistory(st.History, working, start.Unix(), historyCap)
	if len(working) == 0 {
		st.BackoffDays = nextBackoff(st.BackoffDays, cfg)
	} else {
		st.BackoffDays = cfg.baseDays
	}
	// Record this sweep's cert failures
	cf := st.CertFailed
	if cf == nil {
		cf = make(map[string]int64)
	}
	for _, ip := range rep.sweepFails() {
		cf[ip] = start.Unix()
	}
	for _, r := range working {
		delete(cf, r.IP)
	}
	st.CertFailed = state.PruneCertFailed(cf, start.Unix(), certFailTTL, certFailCap)
	st.Working = toWorking(working)
	st.UpdatedUnix = start.Unix()
	if err := state.Save(cfg.statePath, *st); err != nil {
		slog.Error("state save failed", "err", err)
	}

	outcome := fmt.Sprintf("%d working", len(working))
	if len(working) == 0 {
		outcome = "none"
	}
	if cs := rep.summary(); cs != "" {
		outcome += " — " + cs
		slog.Info("certification summary", "outcome", outcome)
	}
	srv.EndRun(start.Unix(), int(time.Since(start).Seconds()), outcome, toResolverInfo(working), len(st.History))
	report(cfg, base.Prober != nil, working)
}

type certReport struct {
	mu        sync.Mutex
	attempts  int
	reasons   map[string]int
	sweepFail []string
}

func newCertReport() *certReport { return &certReport{reasons: make(map[string]int)} }

func (c *certReport) note(accepted bool, reason string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.attempts++
	if accepted {
		return
	}
	key := reason
	if i := strings.IndexByte(key, ':'); i > 0 { // collapse "exec: <detail>" to "exec"
		key = key[:i]
	}
	if key == "" {
		key = "rejected"
	}
	c.reasons[key]++
}

func (c *certReport) addSweepFail(ip string) {
	c.mu.Lock()
	c.sweepFail = append(c.sweepFail, ip)
	c.mu.Unlock()
}

func (c *certReport) sweepFails() []string {
	c.mu.Lock()
	defer c.mu.Unlock()
	return append([]string(nil), c.sweepFail...)
}

func (c *certReport) summary() string {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.attempts == 0 {
		return ""
	}
	out := fmt.Sprintf("certs: %d tried", c.attempts)
	if len(c.reasons) > 0 {
		keys := make([]string, 0, len(c.reasons))
		for k := range c.reasons {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		parts := make([]string, len(keys))
		for i, k := range keys {
			parts[i] = fmt.Sprintf("%s=%d", k, c.reasons[k])
		}
		out += " — " + strings.Join(parts, " ")
	}
	return out
}

type progress struct {
	srv        *api.Server
	start      time.Time
	probed     atomic.Int64
	candidates atomic.Int64

	mu      sync.Mutex
	lastLog time.Time
}

const (
	progressPushEvery = 10              // push the counter to the API every N probes (throttle SSE churn)
	progressLogEvery  = 5 * time.Second // ...and log a heartbeat at most this often
)

func newProgress(srv *api.Server) *progress {
	return &progress{srv: srv, start: time.Now()}
}

// beginPhase grows the candidate denominator and labels the active leg.
func (p *progress) beginPhase(phase string, n int) {
	total := int(p.candidates.Add(int64(n)))
	p.srv.SetPhase(phase, total)
}

// tick is the per-probe callback: bump the counter, push it (throttled), and
// heartbeat the rate/ETA to the log.
func (p *progress) tick() {
	done := p.probed.Add(1)
	if done%progressPushEvery == 0 {
		p.srv.SetProbed(int(done))
	}

	p.mu.Lock()
	now := time.Now()
	if now.Sub(p.lastLog) < progressLogEvery {
		p.mu.Unlock()
		return
	}
	p.lastLog = now
	p.mu.Unlock()

	total := p.candidates.Load()
	elapsed := now.Sub(p.start).Seconds()
	rate := 0.0
	if elapsed > 0 {
		rate = float64(done) / elapsed
	}
	eta := "unknown"
	if rate > 0 && total > done {
		eta = (time.Duration(float64(total-done)/rate) * time.Second).Round(time.Second).String()
	}
	slog.Info("scan progress", "probed", done, "candidates", total,
		"qps", int(rate+0.5), "elapsed", time.Duration(elapsed*float64(time.Second)).Round(time.Second).String(), "eta", eta)
}

// flush pushes the exact final counter (the throttle may have skipped the tail).
func (p *progress) flush() { p.srv.SetProbed(int(p.probed.Load())) }

func (p *progress) total() int { return int(p.probed.Load()) }

// liveSet accumulates accepted resolvers during a cycle.
type liveSet struct {
	mu          sync.Mutex
	cfg         config
	srv         *api.Server
	history     []state.Historic
	backoffDays int
	existing    []string
	lastFlush   time.Time        // last mid-cycle mdns restart (debounce)
	certifyOn   bool             // false = gates-only; never push uncertified resolvers live
	certFailed  map[string]int64 // carried read-only so incremental saves preserve the skip-list
	working     []score.Result
}

func (l *liveSet) onAccept(r score.Result, _ int) {
	l.mu.Lock()
	l.working = upsertResult(l.working, r)
	l.srv.SetAccepted(len(l.working))
	// Crash-safe incremental persist (preserve history/backoff for cycle-end).
	if err := state.Save(l.cfg.statePath, state.State{
		UpdatedUnix: time.Now().Unix(),
		BackoffDays: l.backoffDays,
		Working:     toWorking(l.working),
		History:     l.history,
		CertFailed:  l.certFailed,
	}); err != nil {
		slog.Error("incremental state save failed", "err", err)
	}
	// Persist the resolver file on every certified find so the on-disk list is always
	// current (gates-only survivors stay out — they may not carry mdns at all).
	have := len(l.working)
	var ips []string
	if l.certifyOn {
		ips = union(l.existing, ipsOf(score.RankWorking(l.working)))
	}
	l.mu.Unlock()

	// Write + reload outside the lock (reload may exec a slow restart).
	if ips == nil {
		return // gates-only mode: nothing goes live
	}
	changed, err := assign.WriteManaged(l.cfg.resolversPath, ips)
	if err != nil {
		slog.Error("incremental assign failed", "err", err)
		return
	}
	if !changed {
		return
	}
	// Restart to adopt only below the watermark, once per cooldown.
	l.mu.Lock()
	wantRestart := have <= l.cfg.watermark && time.Since(l.lastFlush) >= l.cfg.flushCooldown
	if wantRestart {
		l.lastFlush = time.Now()
	}
	l.mu.Unlock()
	if wantRestart {
		slog.Info("fed new resolvers to mdns (below watermark)", "resolvers", len(ips), "have", have)
		reload(l.cfg.reloadURL)
	}
}

func (l *liveSet) count() int { l.mu.Lock(); defer l.mu.Unlock(); return len(l.working) }
func (l *liveSet) snapshot() []score.Result {
	l.mu.Lock()
	defer l.mu.Unlock()
	return append([]score.Result(nil), l.working...)
}
func (l *liveSet) ipsSet() map[string]struct{} {
	l.mu.Lock()
	defer l.mu.Unlock()
	return ipsSet(l.working)
}

// nextBackoff doubles the interval, capped at maxDays.
func nextBackoff(days int, cfg config) int {
	if days < cfg.baseDays {
		return cfg.baseDays
	}
	if n := days * 2; n < cfg.maxDays {
		return n
	}
	return cfg.maxDays
}

// mergeHistory upserts the just-worked resolvers (stamped now), then keeps the
// most-recent `cap`.
func mergeHistory(history []state.Historic, working []score.Result, now int64, cap int) []state.Historic {
	idx := make(map[string]int, len(history))
	for i, h := range history {
		idx[h.IP] = i
	}
	for _, r := range working {
		h := state.Historic{IP: r.IP, LastWorkingUnix: now, UploadMTU: r.UploadMTU, DownloadMTU: r.DownloadMTU}
		if i, ok := idx[r.IP]; ok {
			history[i] = h
		} else {
			idx[r.IP] = len(history)
			history = append(history, h)
		}
	}
	sort.SliceStable(history, func(i, j int) bool { return history[i].LastWorkingUnix > history[j].LastWorkingUnix })
	if len(history) > cap {
		history = history[:cap]
	}
	return history
}

func flatten(tiers []targets.Tier, exclude map[string]struct{}, certFailed map[string]int64, now int64, ttl time.Duration, maxProbes int) []string {
	ttlSec := int64(ttl / time.Second)
	var out []string
	for _, t := range tiers {
		for _, ip := range t.IPs {
			if _, skip := exclude[ip]; skip {
				continue
			}
			if ts, bad := certFailed[ip]; bad && now-ts < ttlSec {
				continue // recently failed certification — skip until the TTL expires
			}
			out = append(out, ip)
			if maxProbes > 0 && len(out) >= maxProbes {
				return out
			}
		}
	}
	return out
}

// intersectCount counts how many currently-live resolvers survived into working.
func intersectCount(existing []string, working []score.Result) int {
	have := setOf(existing)
	n := 0
	for _, r := range working {
		if _, ok := have[r.IP]; ok {
			n++
		}
	}
	return n
}

func upsertResult(list []score.Result, r score.Result) []score.Result {
	for i := range list {
		if list[i].IP == r.IP {
			list[i] = r
			return list
		}
	}
	return append(list, r)
}

func union(a, b []string) []string {
	seen := make(map[string]struct{})
	var out []string
	for _, s := range append(append([]string{}, a...), b...) {
		if _, dup := seen[s]; dup {
			continue
		}
		seen[s] = struct{}{}
		out = append(out, s)
	}
	return out
}

func dedup(in []string) []string { return union(in, nil) }

func setOf(ips []string) map[string]struct{} {
	m := make(map[string]struct{}, len(ips))
	for _, ip := range ips {
		m[ip] = struct{}{}
	}
	return m
}

func ipsOf(rs []score.Result) []string {
	out := make([]string, len(rs))
	for i, r := range rs {
		out[i] = r.IP
	}
	return out
}

func ipsSet(rs []score.Result) map[string]struct{} {
	m := make(map[string]struct{}, len(rs))
	for _, r := range rs {
		m[r.IP] = struct{}{}
	}
	return m
}

func toWorking(rs []score.Result) []state.Working {
	ws := make([]state.Working, len(rs))
	for i, r := range rs {
		ws[i] = state.Working{IP: r.IP, UploadMTU: r.UploadMTU, DownloadMTU: r.DownloadMTU, EDNSMax: r.EDNSMax, LossPct: int(r.LossFrac * 100)}
	}
	return ws
}

func toResolverInfo(rs []score.Result) []api.ResolverInfo {
	out := make([]api.ResolverInfo, len(rs))
	for i, r := range rs {
		out[i] = api.ResolverInfo{IP: r.IP, UploadMTU: r.UploadMTU, DownloadMTU: r.DownloadMTU, EDNSMax: r.EDNSMax, LossPct: int(r.LossFrac * 100)}
	}
	return out
}

func reload(url string) {
	if err := assign.Reload(url); err != nil {
		slog.Error("reload failed", "err", err)
	} else if strings.TrimSpace(url) != "" {
		slog.Info("reload triggered")
	}
}

func report(cfg config, certified bool, working []score.Result) {
	fmt.Printf("\n=== %d working resolvers ===\n", len(working))
	for i, r := range working {
		if certified {
			fmt.Printf("%2d. %-15s up=%dB down=%dB loss=%.0f%% certRTT=%dms\n",
				i+1, r.IP, r.UploadMTU, r.DownloadMTU, r.LossFrac*100, r.CertRTTms)
		} else {
			fmt.Printf("%2d. %-15s ednsMax=%d loss=%.0f%% aliveRTT=%dms (gates ok; certify skipped)\n",
				i+1, r.IP, r.EDNSMax, r.LossFrac*100, r.AliveRTT.Milliseconds())
		}
	}
}

// buildProber wires the out-of-process certifier
func buildProber(cfg config) *certify.Prober {
	pr, err := certify.New(certify.Config{
		Bin:        cfg.probeBin,
		ConfigPath: cfg.clientConfig,
		Domain:     cfg.domain,
		Key:        cfg.key,
		Method:     cfg.method,
		BaseEncode: cfg.baseEncode,
		MinUp:      cfg.minUp,
		MaxUp:      cfg.maxUp,
		MinDown:    cfg.minDown,
		MaxDown:    cfg.maxDown,
	})
	switch {
	case errors.Is(err, certify.ErrNoBinary):
		slog.Warn("probe binary missing — running gates only, certification disabled; "+
			"uncertified resolvers will NOT be assigned to the tunnel",
			"MDNS_PROBE_BIN", cfg.probeBin, "err", err)
		return nil
	case err != nil:
		slog.Error("prober init failed", "err", err)
		os.Exit(1)
	case pr == nil:
		slog.Warn("no probe config (set MDNS_CLIENT_CONFIG, or MDNS_DOMAIN+MDNS_ENCRYPTION_KEY) — gates only, skipping certification")
		return nil
	}
	attrs := []any{"bin", cfg.probeBin, "client_config", configSource(cfg)}
	if v := certify.ProbeVersion(cfg.probeBin); v != "" {
		attrs = append(attrs, "version", v)
	}
	slog.Info("prober ready", attrs...)
	return pr
}

// configSource reports where the probe's tunnel config comes from, for logging.
func configSource(cfg config) string {
	if cfg.clientConfig != "" {
		return cfg.clientConfig
	}
	return "generated from env"
}

// setupLogging installs the default slog logger. SCANNER_LOG_FORMAT=json picks
// the JSON handler (else text); SCANNER_LOG_LEVEL sets the minimum level.
func setupLogging() {
	lvl := slog.LevelInfo
	if err := lvl.UnmarshalText([]byte(strings.ToLower(os.Getenv("SCANNER_LOG_LEVEL")))); err != nil {
		lvl = slog.LevelInfo
	}
	opts := &slog.HandlerOptions{Level: lvl}
	var h slog.Handler = slog.NewTextHandler(os.Stderr, opts)
	if strings.EqualFold(os.Getenv("SCANNER_LOG_FORMAT"), "json") {
		h = slog.NewJSONHandler(os.Stderr, opts)
	}
	slog.SetDefault(slog.New(h))
}

func signalContext() context.Context {
	ctx, _ := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	return ctx
}

func loadConfig() config {
	domain := os.Getenv("MDNS_DOMAIN")
	probeDomain := "cloudflare.com"
	cfg := config{
		domain:        domain,
		probeDomain:   probeDomain,
		key:           os.Getenv("MDNS_ENCRYPTION_KEY"),
		method:        atoiEnv("MDNS_DATA_ENCRYPTION_METHOD", 1),
		baseEncode:    atoiEnv("MDNS_BASE_ENCODE_DATA", 0) == 1,
		probeBin:      envOr("MDNS_PROBE_BIN", "mdns-client"),
		clientConfig:  os.Getenv("MDNS_CLIENT_CONFIG"),
		minUp:         atoiEnv("MDNS_MIN_UPLOAD_MTU", 0),
		maxUp:         atoiEnv("MDNS_MAX_UPLOAD_MTU", 0),
		minDown:       atoiEnv("MDNS_MIN_DOWNLOAD_MTU", 0),
		maxDown:       atoiEnv("MDNS_MAX_DOWNLOAD_MTU", 0),
		bindIP:        os.Getenv("SCANNER_BIND_IP"),
		qps:           atoiEnv("SCANNER_QPS", 30),
		cooldown:      time.Duration(atoiEnv("SCANNER_PER24_COOLDOWN_MS", 200)) * time.Millisecond,
		targetN:       atoiEnv("SCANNER_TARGET_N", 15),
		watermark:     atoiEnv("SCANNER_WATERMARK", 8),
		flushCooldown: time.Duration(atoiEnv("SCANNER_FLUSH_COOLDOWN_S", 180)) * time.Second,
		dataDir:       envOr("SCANNER_DATA_DIR", "data"),
		stageTimeout:  time.Duration(atoiEnv("SCANNER_STAGE_TIMEOUT_MS", 1500)) * time.Millisecond,
		daemon:        atoiEnv("SCANNER_DAEMON", 0) == 1,
		baseDays:      atoiEnv("SCANNER_BASE_INTERVAL_DAYS", 1),
		maxDays:       atoiEnv("SCANNER_MAX_INTERVAL_DAYS", 7),
		resolversPath: envOr("SCANNER_RESOLVERS_PATH", "../mdns/client_resolvers.txt"),
		statePath:     envOr("SCANNER_STATE_FILE", "scanner-state.json"),
		reloadURL:     os.Getenv("SCANNER_RELOAD_URL"),
		apiAddr:       envOr("SCANNER_API_ADDR", ":8088"),
		apiSecret:     os.Getenv("SCANNER_API_SECRET"),
	}

	// Fail fast on a malformed bind IP
	if cfg.bindIP != "" && net.ParseIP(cfg.bindIP) == nil {
		slog.Error("invalid SCANNER_BIND_IP", "value", cfg.bindIP)
		os.Exit(1)
	}
	return cfg
}

func atoiEnv(key string, def int) int {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.Atoi(strings.TrimSpace(v)); err == nil {
			return n
		}
		slog.Warn("malformed integer env ignored — using default", "key", key, "value", v, "default", def)
	}
	return def
}

func envOr(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}
