// Package selector rotates the in-use byedpi and sni-spoofing edges off degraded ones. Health is
// tiered: cheap tcp_loss for liveness, then the real-path survival probe for the in-use edge and a
// small candidate budget. Side effects go through an Applier and probing is injected, so the
// decisions are testable without network or docker.
package selector

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"os"
	"slices"

	"github.com/0xf00f00/cf-edge-manager/internal/decoy"
)

// Applier performs the edge-path side-effects (see package apply).
type Applier interface {
	CurrentByedpi() string
	CurrentSnispoof() string
	CurrentFakeSNI() string
	WriteByedpi(ip string) error
	WriteSnispoof(ip string) error
	WriteFakeSNI(sni string) error
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
	NoByedpi           bool    // manage the snispoof path only

	ConfirmTicks int   // consecutive bad checks before an edge is replaced; a good check resets the count
	MaxRotations int   // edge changes per path per 24h before the circuit breaker stops further ones (0 = unlimited)
	HoldInterval int64 // recheck cadence while the network isn't trusted (s)
}

// Ack is the passive verdict on an instance's running edge.
type Ack struct {
	Ratio    float64 // share of recent connections that hit an ACK timeout
	Samples  int     // connections in that window
	Bad      bool    // credibly above the limit
	Systemic bool    // every instance with enough traffic is bad: not one edge's fault
}

// Peers reports the edges and decoys the other sni-spoofing instances are on, so
// instances stay on different edges and different decoys.
type Peers func() (edges, decoys []string)

// State is the durable picker state.
type State struct {
	Fails          int              `json:"fails"`
	NextRun        int64            `json:"next_run"`
	LastRestart    int64            `json:"last_restart"`
	RestartPending bool             `json:"restart_pending,omitempty"` // snispoof config edited since the last restart
	Quarantine     map[string]int64 `json:"quarantine"`

	DecoyCheckAfter int64  `json:"decoy_check_after,omitempty"`
	DecoyDead       string `json:"decoy_dead,omitempty"` // live decoy judged blocked by name

	Hold      string             `json:"hold,omitempty"`      // why the last tick took no action (network not trusted)
	Bad       map[string]int     `json:"bad,omitempty"`       // per path: consecutive checks that found the edge bad
	Rotations map[string][]int64 `json:"rotations,omitempty"` // per path: when the edge was last changed (24h)
}

// Selector holds policy + injected probes/IO.
type Selector struct {
	cfg       Config
	log       *slog.Logger
	loss      func(ip string) float64            // tcp_loss tier
	survival  func(ip string, strict bool) *bool // real-path tier (strict: adopting a candidate); nil == no signal
	pool      func() []string                    // best-first scanned pool
	apply     Applier
	statePath string

	hello  decoy.Check                // plain TLS hello; nil == decoy self-healing off
	trial  func(ip, sni string) *bool // real-path probe with decoy sni
	decoys []string                   // candidate decoys, preferred first
	Hooks
}

// Hooks are the optional collaborators; the zero value is a lone, ungated selector.
type Hooks struct {
	Peers Peers         // other instances' edges and decoys to stay clear of
	Ack   func() Ack    // passive handshake signal for this instance
	Hold  func() string // why nothing should change right now ("" = go ahead)
}

// Use installs hooks; unset fields keep their defaults.
func (s *Selector) Use(h Hooks) {
	if h.Peers != nil {
		s.Peers = h.Peers
	}
	if h.Ack != nil {
		s.Ack = h.Ack
	}
	if h.Hold != nil {
		s.Hold = h.Hold
	}
}

// New builds a Selector from config, injected probes (loss/survival), a pool
// reader, and an Applier for side-effects.
func New(cfg Config, log *slog.Logger, statePath string,
	loss func(string) float64, survival func(string, bool) *bool, pool func() []string, apply Applier,
) *Selector {
	return &Selector{
		cfg: cfg, log: log, statePath: statePath, loss: loss, survival: survival, pool: pool, apply: apply,
		Hooks: Hooks{
			Peers: func() ([]string, []string) { return nil, nil },
			Ack:   func() Ack { return Ack{} },
			Hold:  func() string { return "" },
		},
	}
}

func (s *Selector) loadState() State {
	st := State{Quarantine: map[string]int64{}}
	if b, err := os.ReadFile(s.statePath); err == nil {
		_ = json.Unmarshal(b, &st)
	}
	if st.Quarantine == nil {
		st.Quarantine = map[string]int64{}
	}
	if st.Bad == nil {
		st.Bad = map[string]int{}
	}
	if st.Rotations == nil {
		st.Rotations = map[string][]int64{}
	}
	return st
}

func (s *Selector) saveState(st State) {
	if b, err := json.Marshal(st); err == nil {
		_ = os.WriteFile(s.statePath, b, 0o644) //nolint:gosec
	}
}

// edgeOK is the tiered health check: cheap tcp_loss first (a dead edge is
// condemned without paying for the probe), then the real-path probe. A nil survival
// (no signal) keeps the edge: never blame an edge for the prober being unavailable.
func (s *Selector) edgeOK(ip string, lossMax float64, label string, useSurvival bool) bool {
	loss := s.loss(ip)
	if loss > lossMax {
		s.log.Info("edge bad: tcp_loss", "label", label, "ip", ip, "loss", loss, "max", lossMax)
		return false
	}
	if !s.cfg.SurvivalCheck || !useSurvival {
		s.log.Info("edge ok", "label", label, "ip", ip, "loss", loss)
		return true
	}
	survived := s.survival(ip, false)
	if survived != nil && !*survived {
		s.log.Info("edge bad: real-path FAILED (loss-blind case)", "label", label, "ip", ip, "loss", loss)
		return false
	}
	s.log.Info("edge ok", "label", label, "ip", ip, "loss", loss, "real_path", survStr(survived))
	return true
}

// pickClean returns the best pool IP that is clean and != exclude. Only the first MaxProbeCandidates
// tcp_loss-clean ones get the real-path probe; if none is confirmed it falls back to the first unprobed
// one. A known-bad edge is never returned; realPath=false picks on tcp_loss alone.
func (s *Selector) pickClean(pool []string, exclude string, q map[string]int64, now int64, realPath bool) string {
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
		if !s.cfg.SurvivalCheck || !realPath {
			return ip
		}
		if probed < s.cfg.MaxProbeCandidates {
			probed++
			survived := s.survival(ip, true)
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

// settle returns (ip_in_use, healthy) for one path, repointing it if bad. A bad reading
// from any source must persist for ConfirmTicks checks; a good check resets the count.
func (s *Selector) settle(name, current string, pool []string, other string,
	q map[string]int64, now int64, realPath bool, degraded string, st *State, apply func(string),
) (string, bool) {
	if current != "" {
		if degraded != "" {
			s.log.Info("edge bad: "+degraded, "path", name, "ip", current)
		}
		if degraded == "" && s.edgeOK(current, s.cfg.KeepMax, name+" keep", realPath) {
			delete(st.Bad, name)
			delete(q, current)
			s.log.Info("path healthy", "path", name, "ip", current)
			return current, true
		}
		if st.Bad[name]++; st.Bad[name] < s.cfg.ConfirmTicks {
			s.log.Info("path bad; replacing it only if that persists", "path", name, "ip", current, "checks", st.Bad[name], "need", s.cfg.ConfirmTicks)
			return current, true
		}
	}
	s.log.Info("path degraded/unset; seeking replacement", "path", name, "ip", current)
	if current != "" && !s.rotationAllowed(st, name, now) {
		s.log.Warn("rotation budget spent; keeping the edge", "path", name, "ip", current, "max_per_day", s.cfg.MaxRotations)
		return current, false
	}
	if current != "" {
		q[current] = now + s.cfg.QuarantineTTL
		s.log.Info("quarantined", "ip", current, "ttl", s.cfg.QuarantineTTL)
	}
	next := s.pickClean(pool, other, q, now, realPath)
	if next == "" {
		s.log.Info("no clean candidate; leaving", "path", name, "ip", current)
		return current, false
	}
	apply(next)
	delete(st.Bad, name)
	st.Rotations[name] = append(st.Rotations[name], now)
	return next, true
}

// rotationAllowed is the circuit breaker: each change costs a restart, and frequent
// swaps mean the edge is not the problem.
func (s *Selector) rotationAllowed(st *State, name string, now int64) bool {
	recent := slices.DeleteFunc(slices.Clone(st.Rotations[name]), func(t int64) bool { return t <= now-86400 })
	st.Rotations[name] = recent
	return s.cfg.MaxRotations <= 0 || len(recent) < s.cfg.MaxRotations
}

// Tick runs one decision pass and returns the seconds until the next due run.
func (s *Selector) Tick(now int64) int64 {
	st := s.loadState()
	if now < st.NextRun {
		return st.NextRun - now
	}
	if why := s.Hold(); why != "" {
		return s.hold(&st, now, why)
	}
	if st.Hold != "" {
		s.log.Info("network trusted again; resuming", "was", st.Hold)
		st.Hold = ""
	}
	peerEdges, _ := s.Peers()
	pool := slices.DeleteFunc(slices.Clone(s.pool()), func(ip string) bool { return slices.Contains(peerEdges, ip) })
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

	var bip string
	if !s.cfg.NoByedpi {
		bip = s.apply.CurrentByedpi()
	}
	sip := s.apply.CurrentSnispoof()
	s.log.Info("current", "byedpi", bip, "snispoof", sip, "pool_size", len(pool))
	switched := s.checkDecoy(&st, now, pool, sip)
	// byedpi doesn't carry the decoy, so a dead decoy's real-path verdicts say nothing about its edge
	byedpiRealPath := st.DecoyDead == "" || st.DecoyDead != s.apply.CurrentFakeSNI()
	if !byedpiRealPath {
		s.log.Warn("decoy dead; byedpi edge judged on tcp_loss only", "sni", st.DecoyDead)
	}

	applyB := func(ip string) {
		if err := s.apply.WriteByedpi(ip); err != nil {
			s.log.Error("write byedpi config", "err", err)
		} else {
			s.log.Info("byedpi path ->", "ip", ip)
		}
	}
	applyS := func(ip string) {
		if err := s.apply.WriteSnispoof(ip); err != nil {
			s.log.Error("write snispoof config", "err", err)
			return
		}
		st.RestartPending = true
		s.log.Info("snispoof path ->", "ip", ip)
	}

	paths, healthy := 1, 0
	if !s.cfg.NoByedpi {
		var bok bool
		bip, bok = s.settle("byedpi", bip, pool, sip, q, now, byedpiRealPath, "", &st, applyB)
		paths, healthy = 2, b2i(bok)
	}
	sip, sok := s.settle("snispoof", sip, pool, bip, q, now, true, s.ackDegraded(st), &st, applyS)
	healthy += b2i(sok)

	// distinct-IP guard: the paths must not share fate.
	if (bip != "" && bip == sip) || slices.Contains(peerEdges, sip) {
		s.log.Warn("path shares an edge with another path; diversifying snispoof", "ip", sip)
		if next := s.pickClean(pool, bip, q, now, true); next != "" {
			applyS(next)
		} else {
			s.log.Warn("pool lacks a 2nd distinct clean IP; leaving both", "ip", sip)
		}
	}
	// one restart carries every staged snispoof edit; a new decoy doesn't wait out MinRestartGap
	if st.RestartPending {
		if !switched && now-st.LastRestart < s.cfg.MinRestartGap {
			s.log.Info("snispoof restart rate-limited; staged config goes live on a later tick")
		} else if err := s.apply.RestartSnispoof(); err != nil {
			s.log.Warn("restart snispoof failed; retrying next tick", "err", err)
		} else {
			st.RestartPending, st.LastRestart = false, now
			s.log.Info("restarted snispoof")
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
		s.log.Info("outage: no healthy path, no clean candidates; backing off", "paths", paths, "next_s", delay, "fails", st.Fails)
	}
	st.NextRun = now + delay
	s.saveState(st)
	return delay
}

// hold takes no action: with the network down or lossy no edge reading means anything.
func (s *Selector) hold(st *State, now int64, why string) int64 {
	if st.Hold == "" {
		s.log.Warn("network not trusted; taking no action until it settles", "state", why)
	}
	st.Hold, st.Bad = why, map[string]int{}
	st.NextRun = now + s.cfg.HoldInterval
	s.saveState(*st)
	return s.cfg.HoldInterval
}

// ackDegraded is why the running edge should go despite passing its probes. Empty when
// there is no signal, when every instance fails alike (not this edge's fault), or while a
// staged restart means the log still describes the previous edge.
func (s *Selector) ackDegraded(st State) string {
	if st.RestartPending {
		return ""
	}
	a := s.Ack()
	switch {
	case !a.Bad:
		return ""
	case a.Systemic:
		s.log.Warn("every instance is failing handshakes; not rotating on that signal", "ratio", a.Ratio, "samples", a.Samples)
		return ""
	}
	return fmt.Sprintf("%.0f%% of %d recent connections hit an ACK timeout", a.Ratio*100, a.Samples)
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
