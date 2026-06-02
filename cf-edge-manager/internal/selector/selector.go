// Package selector is the runtime guardian (the old cf-edge-picker's policy
// half): every tick it checks the in-use byedpi + sni-spoofing edges and rotates
// them off a degraded edge, picking replacements from the scanned pool. It is
// loss-blind no longer — health is TIERED: cheap tcp_loss for liveness, then the
// expensive real-path survival probe (only for edges that passed tcp_loss, only
// the in-use edge + a small candidate budget). Side-effects (config rewrites,
// container restart) are delegated to an Applier; probing is injected, so the
// decision logic is unit-testable without network or docker.
package selector

import (
	"encoding/json"
	"log/slog"
	"os"
)

// Applier performs the edge-path side-effects (see package apply).
type Applier interface {
	CurrentByedpi() string
	CurrentSnispoof() string
	WriteByedpi(ip string) error
	WriteSnispoof(ip string) error
	RestartSnispoof() error
}

// Config mirrors the picker's knobs (durations in seconds).
type Config struct {
	KeepMax            float64 // keep an in-use edge if tcp_loss <= this
	PickMax            float64 // a replacement must be this clean (tcp_loss)
	MaxCandidates      int     // pool IPs examined per pick
	MaxProbeCandidates int     // of those, how many get the expensive real-path probe
	BaseInterval       int64   // normal cadence (s)
	MaxBackoff         int64   // cap during a sustained outage (s)
	MinRestartGap      int64   // min seconds between snispoof restarts
	QuarantineTTL      int64   // skip a confirmed-bad in-use IP this long (s)
	SurvivalCheck      bool    // gate the real-path tier (false = loss-only)
}

// State is the durable picker state.
type State struct {
	Fails       int              `json:"fails"`
	NextRun     int64            `json:"next_run"`
	LastRestart int64            `json:"last_restart"`
	Quarantine  map[string]int64 `json:"quarantine"`
}

// Selector holds policy + injected probes/IO.
type Selector struct {
	cfg       Config
	log       *slog.Logger
	loss      func(ip string) float64 // tcp_loss tier
	survival  func(ip string) *bool   // real-path tier; nil == no signal
	pool      func() []string         // best-first scanned pool
	apply     Applier
	statePath string
}

// New builds a Selector from config, injected probes (loss/survival), a pool
// reader, and an Applier for side-effects.
func New(cfg Config, log *slog.Logger, statePath string,
	loss func(string) float64, survival func(string) *bool, pool func() []string, apply Applier,
) *Selector {
	return &Selector{cfg: cfg, log: log, statePath: statePath, loss: loss, survival: survival, pool: pool, apply: apply}
}

func (s *Selector) loadState() State {
	st := State{Quarantine: map[string]int64{}}
	if b, err := os.ReadFile(s.statePath); err == nil {
		_ = json.Unmarshal(b, &st)
	}
	if st.Quarantine == nil {
		st.Quarantine = map[string]int64{}
	}
	return st
}

func (s *Selector) saveState(st State) {
	if b, err := json.Marshal(st); err == nil {
		_ = os.WriteFile(s.statePath, b, 0o644) //nolint:gosec
	}
}

// edgeOK is the tiered health check: cheap tcp_loss first (a dead edge is
// rejected without paying for the probe), then the real-path survival probe.
// survived==false rotates the edge; survived==nil (no signal) keeps it (we never
// blame an edge for the prober being unavailable).
func (s *Selector) edgeOK(ip string, lossMax float64, label string, useSurvival bool) bool {
	loss := s.loss(ip)
	if loss > lossMax {
		s.log.Info("edge rejected: tcp_loss", "label", label, "ip", ip, "loss", loss, "max", lossMax)
		return false
	}
	if !s.cfg.SurvivalCheck || !useSurvival {
		return true
	}
	survived := s.survival(ip)
	if survived != nil && !*survived {
		s.log.Info("edge rejected: real-path FAILED (loss-blind case)", "label", label, "ip", ip, "loss", loss)
		return false
	}
	return true
}

// pickClean returns the best-first pool IP that is clean and != exclude. Only the
// first MaxProbeCandidates tcp_loss-clean candidates get the expensive probe;
// if the budget is spent without a confirmed survivor, fall back to the first
// tcp_loss-clean-but-unprobed edge. A known-real-path-bad edge is never returned.
func (s *Selector) pickClean(pool []string, exclude string, q map[string]int64, now int64) string {
	candidates := make([]string, 0, len(pool))
	for _, ip := range pool {
		if ip != exclude {
			candidates = append(candidates, ip)
		}
	}
	available := make([]string, 0, len(candidates))
	for _, ip := range candidates {
		if q[ip] <= now {
			available = append(available, ip)
		}
	}
	if len(available) == 0 {
		available = candidates // every candidate quarantined -> reconsider all
	}
	if len(available) > s.cfg.MaxCandidates {
		available = available[:s.cfg.MaxCandidates]
	}

	probed := 0
	fallback := ""
	for _, ip := range available {
		loss := s.loss(ip)
		if loss > s.cfg.PickMax {
			s.log.Info("candidate rejected: tcp_loss", "ip", ip, "loss", loss, "max", s.cfg.PickMax)
			continue
		}
		if !s.cfg.SurvivalCheck {
			return ip
		}
		if probed < s.cfg.MaxProbeCandidates {
			probed++
			survived := s.survival(ip)
			if survived != nil && !*survived {
				s.log.Info("candidate rejected: real-path FAILED", "ip", ip, "loss", loss)
				continue
			}
			s.log.Info("candidate ok", "ip", ip, "loss", loss, "real_path", survStr(survived))
			return ip
		}
		if fallback == "" {
			fallback = ip
		}
	}
	if fallback != "" {
		s.log.Info("pick: probe budget spent; falling back to tcp_loss-clean (unprobed)", "ip", fallback)
	}
	return fallback
}

// settle returns (ip_in_use, healthy) for one path, repointing it if degraded.
func (s *Selector) settle(name, current string, pool []string, other string,
	q map[string]int64, now int64, apply func(string),
) (string, bool) {
	if current != "" && s.edgeOK(current, s.cfg.KeepMax, name+" keep", true) {
		delete(q, current)
		s.log.Info("path healthy", "path", name, "ip", current)
		return current, true
	}
	s.log.Info("path degraded/unset; seeking replacement", "path", name, "ip", current)
	if current != "" {
		q[current] = now + s.cfg.QuarantineTTL
		s.log.Info("quarantined", "ip", current, "ttl", s.cfg.QuarantineTTL)
	}
	next := s.pickClean(pool, other, q, now)
	if next != "" {
		apply(next)
		return next, true
	}
	s.log.Info("no clean candidate; leaving", "path", name, "ip", current)
	return current, false
}

func (s *Selector) applySnispoof(ip string, st *State, now int64) {
	if err := s.apply.WriteSnispoof(ip); err != nil {
		s.log.Error("write snispoof config", "err", err)
		return
	}
	s.log.Info("snispoof path ->", "ip", ip)
	if now-st.LastRestart >= s.cfg.MinRestartGap {
		if err := s.apply.RestartSnispoof(); err != nil {
			s.log.Warn("restart snispoof failed; new IP applies on next restart", "err", err)
		} else {
			st.LastRestart = now
			s.log.Info("restarted snispoof")
		}
	} else {
		s.log.Info("snispoof restart rate-limited; config staged for next restart")
	}
}

// Tick runs one decision pass and returns the seconds until the next due run.
func (s *Selector) Tick(now int64) int64 {
	st := s.loadState()
	if now < st.NextRun {
		return st.NextRun - now
	}
	pool := s.pool()
	if len(pool) == 0 {
		s.log.Info("pool empty/missing; keeping current edges")
		st.NextRun = now + s.cfg.BaseInterval
		s.saveState(st)
		return s.cfg.BaseInterval
	}
	// prune expired quarantine entries
	q := map[string]int64{}
	for ip, exp := range st.Quarantine {
		if exp > now {
			q[ip] = exp
		}
	}
	st.Quarantine = q

	bip, sip := s.apply.CurrentByedpi(), s.apply.CurrentSnispoof()
	s.log.Info("current", "byedpi", bip, "snispoof", sip, "pool_size", len(pool))

	applyB := func(ip string) {
		if err := s.apply.WriteByedpi(ip); err != nil {
			s.log.Error("write byedpi config", "err", err)
		} else {
			s.log.Info("byedpi path ->", "ip", ip)
		}
	}
	applyS := func(ip string) { s.applySnispoof(ip, &st, now) }

	bip, bok := s.settle("byedpi", bip, pool, sip, q, now, applyB)
	sip, sok := s.settle("snispoof", sip, pool, bip, q, now, applyS)
	healthy := b2i(bok) + b2i(sok)

	// distinct-IP guard: the two paths must not share fate.
	if bip != "" && bip == sip {
		s.log.Warn("both paths on same ip; diversifying snispoof", "ip", bip)
		if next := s.pickClean(pool, bip, q, now); next != "" {
			s.applySnispoof(next, &st, now)
		} else {
			s.log.Warn("pool lacks a 2nd distinct clean IP; leaving both", "ip", bip)
		}
	}

	var delay int64
	if healthy >= 1 {
		st.Fails = 0
		delay = s.cfg.BaseInterval
		s.log.Info("ok", "healthy", healthy, "next_s", delay)
	} else {
		st.Fails++
		shift := st.Fails
		if shift > 16 {
			shift = 16
		}
		delay = s.cfg.BaseInterval << uint(shift)
		if delay > s.cfg.MaxBackoff || delay <= 0 {
			delay = s.cfg.MaxBackoff
		}
		s.log.Info("outage: 0/2 healthy, no clean candidates; backing off", "next_s", delay, "fails", st.Fails)
	}
	st.NextRun = now + delay
	s.saveState(st)
	return delay
}

func b2i(b bool) int {
	if b {
		return 1
	}
	return 0
}

func survStr(b *bool) string {
	if b == nil {
		return "no-signal"
	}
	if *b {
		return "survived"
	}
	return "failed"
}
