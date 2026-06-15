// Package api exposes the scanner's status, a control surface, and a live SSE
// event stream, for the dashboard to surface run info and drive a scan.
//
// Endpoints (all but /healthz require the shared secret when one is configured,
// via the X-Scanner-Secret header):
//
//	GET  /healthz      -> liveness only, unauthenticated, leaks nothing
//	GET  /scan         -> JSON snapshot (state, progress, last run, working set…)
//	GET  /scan/events  -> text/event-stream; full snapshot on every change + heartbeats
//	POST /scan/start   -> begin a scan
//	POST /scan/pause   -> suspend the running scan
//	POST /scan/resume  -> resume a paused scan
//	POST /scan/stop    -> cancel the running/paused scan
//
// Each action is single-concern and idempotent in effect (requesting the state
// you are already in is a 200 no-op), mirroring Docker's start/stop/pause verbs.
// Control is a small state machine: idle -> scanning <-> paused, and any active
// state -> idle via stop. Pause genuinely suspends the worker pool (via the Wait
// gate) rather than abandoning progress; stop cancels the run.
package api

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"sync"
	"time"
)

// Scan lifecycle states surfaced in the snapshot and driven by the control API.
const (
	StateIdle     = "idle"
	StateScanning = "scanning"
	StatePaused   = "paused"
	StateStopping = "stopping"
)

// sseHeartbeat is how often an idle SSE stream emits a keepalive comment.
const sseHeartbeat = 15 * time.Second

type ResolverInfo struct {
	IP          string `json:"ip"`
	UploadMTU   int    `json:"up_mtu,omitempty"`
	DownloadMTU int    `json:"down_mtu,omitempty"`
	EDNSMax     int    `json:"edns_max,omitempty"`
	LossPct     int    `json:"loss_pct"`
	Backup      bool   `json:"backup,omitempty"` // admitted via the backup tier
}

// Funnel is the per-stage survivor count of the last sweep — how many candidates
// made it past each gate. A cliff at one stage localises a problem at a glance.
type Funnel struct {
	Probed  int `json:"probed"`
	Alive   int `json:"alive"`
	NX      int `json:"nx"`
	Forward int `json:"forward"`
	EDNS    int `json:"edns"`
	Upload  int `json:"upload"`
	Gates   int `json:"gates"`
	Cert    int `json:"cert"`
}

// errorResponse is the body returned for 4xx responses.
type errorResponse struct {
	Error string `json:"error"`
}

// healthResponse is the body of GET /healthz.
type healthResponse struct {
	Status string `json:"status"`
}

type Snapshot struct {
	State              string         `json:"state"`
	Scanning           bool           `json:"scanning"`
	Paused             bool           `json:"paused"`
	RunStartedUnix     int64          `json:"run_started_unix,omitempty"`
	Phase              string         `json:"phase"`      // "verify" | "sweep" while scanning, else ""
	Candidates         int            `json:"candidates"` // IPs to check this run so far (the Probed denominator)
	Probed             int            `json:"probed"`
	Accepted           int            `json:"accepted"`
	LastRunUnix        int64          `json:"last_run_unix"`
	LastRunDurationSec int            `json:"last_run_duration_sec"`
	LastOutcome        string         `json:"last_outcome"`
	WorkingCount       int            `json:"working_count"`
	Working            []ResolverInfo `json:"working"`
	HistoryCount       int            `json:"history_count"`
	NextScanUnix       int64          `json:"next_scan_unix"`
	IntervalDays       int            `json:"interval_days"`
	TargetN            int            `json:"target_n,omitempty"`

	// Last-cycle diagnostics (funnel pushed live; backup count set at cycle end).
	Funnel      Funnel `json:"funnel"`
	BackupCount int    `json:"backup_count"`
}

type Server struct {
	mu      sync.Mutex
	snap    Snapshot
	trigger chan struct{}
	secret  string

	// cancel stops the current run; resumeCh is closed to release the Wait gate.
	cancel   context.CancelFunc
	resumeCh chan struct{}

	subs map[chan []byte]struct{} // SSE subscribers
}

// NewServer builds the API server. secret gates every endpoint except /healthz;
// an empty secret disables auth (intended only for local/dev runs).
func NewServer(secret string) *Server {
	return &Server{
		trigger: make(chan struct{}, 1),
		secret:  secret,
		snap:    Snapshot{State: StateIdle},
		subs:    make(map[chan []byte]struct{}),
	}
}

// AuthEnabled reports whether a secret is configured (false = open, dev only).
func (s *Server) AuthEnabled() bool { return s.secret != "" }

// Trigger is signalled when a scan start is requested via the control API.
func (s *Server) Trigger() <-chan struct{} { return s.trigger }

func (s *Server) Get() Snapshot {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.snap
}

// update mutates the snapshot and publishes it, so every change streams to SSE.
func (s *Server) update(f func(*Snapshot)) {
	s.mu.Lock()
	f(&s.snap)
	s.publishLocked()
	s.mu.Unlock()
}

func (s *Server) publishLocked() {
	if len(s.subs) == 0 {
		return
	}
	b, err := json.Marshal(s.snap)
	if err != nil {
		return
	}
	for ch := range s.subs {
		select {
		case ch <- b:
		default: // drop on a slow consumer; the next change carries fresh state
		}
	}
}

// ----- run lifecycle (called by the scan loop) -----------------------------

// BeginRun marks a run active and registers cancel so the control API can stop
// it. cancel must abort the run's context.
func (s *Server) BeginRun(startUnix int64, cancel context.CancelFunc) {
	s.update(func(p *Snapshot) {
		p.State = StateScanning
		p.Scanning = true
		p.Paused = false
		p.RunStartedUnix = startUnix
		p.Phase = ""
		p.Candidates = 0
		p.Probed = 0
		p.Accepted = 0
		// Clear the previous run's diagnostics so the new run builds them fresh.
		p.Funnel = Funnel{}
		p.BackupCount = 0
	})
	s.mu.Lock()
	s.cancel = cancel
	s.resumeCh = nil
	s.mu.Unlock()
}

func (s *Server) SetProbed(n int)   { s.update(func(p *Snapshot) { p.Probed = n }) }
func (s *Server) SetAccepted(n int) { s.update(func(p *Snapshot) { p.Accepted = n }) }

// SetPhase records which leg of the cycle is running ("verify"/"sweep") and the
// cumulative candidate count, so the dashboard can show probed-of-candidates.
func (s *Server) SetPhase(phase string, candidates int) {
	s.update(func(p *Snapshot) {
		p.Phase = phase
		p.Candidates = candidates
	})
}

func (s *Server) EndRun(startUnix int64, durSec int, outcome string, working []ResolverInfo, historyCount int) {
	s.mu.Lock()
	s.cancel = nil
	if s.resumeCh != nil { // release any pause gate so workers don't leak
		close(s.resumeCh)
		s.resumeCh = nil
	}
	s.snap.State = StateIdle
	s.snap.Scanning = false
	s.snap.Paused = false
	s.snap.Phase = ""
	s.snap.LastRunUnix = startUnix
	s.snap.LastRunDurationSec = durSec
	s.snap.LastOutcome = outcome
	s.snap.WorkingCount = len(working)
	s.snap.Working = working
	s.snap.HistoryCount = historyCount
	s.publishLocked()
	s.mu.Unlock()
}

// SetTarget records the per-cycle resolver target (the dashboard shows
// "accepted of target" progress). Set once at startup; it doesn't change.
func (s *Server) SetTarget(n int) { s.update(func(p *Snapshot) { p.TargetN = n }) }

// SetFunnel updates just the live per-stage funnel. Pushed periodically during
// the sweep so the breakdown builds up mid-run and is preserved if the user
// pauses or stops partway.
func (s *Server) SetFunnel(f Funnel) { s.update(func(p *Snapshot) { p.Funnel = f }) }

// SetCycleStats records the last cycle's funnel and backup count. Called just
// before EndRun so the values persist into the idle snapshot.
func (s *Server) SetCycleStats(f Funnel, backupCount int) {
	s.update(func(p *Snapshot) {
		p.Funnel = f
		p.BackupCount = backupCount
	})
}

func (s *Server) SetSchedule(nextUnix int64, intervalDays int) {
	s.update(func(p *Snapshot) {
		p.NextScanUnix = nextUnix
		p.IntervalDays = intervalDays
	})
}

// Wait is the pause gate. Workers call it before each unit of work; it blocks
// while the run is paused and returns ctx.Err() once unpaused or cancelled.
func (s *Server) Wait(ctx context.Context) error {
	for {
		s.mu.Lock()
		resume := s.resumeCh
		s.mu.Unlock()
		if resume == nil {
			return ctx.Err()
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-resume:
		}
	}
}

// ----- control state machine -----------------------------------------------

// Each transition returns the HTTP status to reply with and a message (non-2xx
// only); the no-op case returns 200.

// start begins a scan.
func (s *Server) start() (int, string) {
	s.mu.Lock()
	switch s.snap.State {
	case StatePaused:
		s.mu.Unlock()
		return http.StatusConflict, "scan is paused; use /scan/resume"
	case StateScanning:
		s.mu.Unlock()
		return http.StatusOK, "" // already running
	default: // idle / stopping
		s.mu.Unlock()
		select { // wake the scan loop; a pending trigger is enough
		case s.trigger <- struct{}{}:
		default:
		}
		return http.StatusAccepted, ""
	}
}

// pause suspends the running scan.
func (s *Server) pause() (int, string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	switch s.snap.State {
	case StateScanning:
		s.pauseLocked()
		return http.StatusOK, ""
	case StatePaused:
		return http.StatusOK, ""
	default:
		return http.StatusConflict, "no scan in progress to pause"
	}
}

// resume continues a paused scan.
func (s *Server) resume() (int, string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	switch s.snap.State {
	case StatePaused:
		s.resumeLocked()
		return http.StatusOK, ""
	case StateScanning:
		return http.StatusOK, ""
	default:
		return http.StatusConflict, "no paused scan to resume"
	}
}

// stop cancels the running or paused scan.
func (s *Server) stop() (int, string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.snap.State == StateScanning || s.snap.State == StatePaused {
		s.stopLocked()
	}
	return http.StatusOK, "" // already idle is fine
}

func (s *Server) pauseLocked() {
	s.resumeCh = make(chan struct{})
	s.snap.State = StatePaused
	s.snap.Paused = true
	s.snap.Scanning = false // paused is not actively scanning; keep the flags exclusive
	slog.Info("scan paused")
	s.publishLocked()
}

func (s *Server) resumeLocked() {
	if s.resumeCh != nil {
		close(s.resumeCh)
		s.resumeCh = nil
	}
	s.snap.State = StateScanning
	s.snap.Paused = false
	s.snap.Scanning = true
	slog.Info("scan resumed")
	s.publishLocked()
}

func (s *Server) stopLocked() {
	s.snap.State = StateStopping
	s.snap.Paused = false
	slog.Info("scan stop requested")
	if s.resumeCh != nil { // unblock paused workers so they observe the cancel
		close(s.resumeCh)
		s.resumeCh = nil
	}
	s.publishLocked()
	if s.cancel != nil {
		s.cancel()
	}
}

// ----- HTTP layer ----------------------------------------------------------

func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", s.handleHealthz)
	mux.HandleFunc("GET /scan", s.auth(s.handleSnapshot))
	mux.HandleFunc("GET /scan/events", s.auth(s.handleEvents))
	mux.HandleFunc("POST /scan/start", s.auth(s.action(s.start)))
	mux.HandleFunc("POST /scan/pause", s.auth(s.action(s.pause)))
	mux.HandleFunc("POST /scan/resume", s.auth(s.action(s.resume)))
	mux.HandleFunc("POST /scan/stop", s.auth(s.action(s.stop)))
	return securityHeaders(mux)
}

// handleHealthz is the liveness probe.
func (s *Server) handleHealthz(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, healthResponse{Status: "ok"})
}

// auth enforces the shared secret (constant-time) when one is configured.
func (s *Server) auth(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if s.secret != "" {
			got := r.Header.Get("X-Scanner-Secret")
			if subtle.ConstantTimeCompare([]byte(got), []byte(s.secret)) != 1 {
				w.Header().Set("WWW-Authenticate", "Scanner")
				writeJSON(w, http.StatusUnauthorized, errorResponse{Error: "unauthorized"})
				return
			}
		}
		next(w, r)
	}
}

func securityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		h := w.Header()
		h.Set("X-Content-Type-Options", "nosniff")
		h.Set("Referrer-Policy", "no-referrer")
		h.Set("Cache-Control", "no-store")
		next.ServeHTTP(w, r)
	})
}

// handleSnapshot returns the current scanner snapshot.
func (s *Server) handleSnapshot(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, s.Get())
}

// action adapts a transition into a handler: reply with the error message on
// failure, else the resulting snapshot. Actions take no body.
func (s *Server) action(fn func() (int, string)) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		status, msg := fn()
		if status >= 400 {
			writeJSON(w, status, errorResponse{Error: msg})
			return
		}
		writeJSON(w, status, s.Get())
	}
}

// handleEvents streams snapshots over Server-Sent Events.
func (s *Server) handleEvents(w http.ResponseWriter, r *http.Request) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming unsupported", http.StatusInternalServerError)
		return
	}
	h := w.Header()
	h.Set("Content-Type", "text/event-stream")
	h.Set("Cache-Control", "no-cache")
	h.Set("Connection", "keep-alive")
	h.Set("X-Accel-Buffering", "no") // tell reverse proxies not to buffer
	w.WriteHeader(http.StatusOK)

	ch := s.subscribe()
	defer s.unsubscribe(ch)

	ctx := r.Context()
	ticker := time.NewTicker(sseHeartbeat)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case b := <-ch:
			if _, err := fmt.Fprintf(w, "event: snapshot\ndata: %s\n\n", b); err != nil {
				return
			}
			flusher.Flush()
		case <-ticker.C:
			if _, err := io.WriteString(w, ": keepalive\n\n"); err != nil {
				return
			}
			flusher.Flush()
		}
	}
}

func (s *Server) subscribe() chan []byte {
	ch := make(chan []byte, 8)
	s.mu.Lock()
	s.subs[ch] = struct{}{}
	if b, err := json.Marshal(s.snap); err == nil { // prime with current state
		ch <- b
	}
	s.mu.Unlock()
	return ch
}

func (s *Server) unsubscribe(ch chan []byte) {
	s.mu.Lock()
	delete(s.subs, ch)
	s.mu.Unlock()
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

// Run serves the API on addr (blocking) until ctx is cancelled, then drains
// in-flight requests with a bounded grace period.
func (s *Server) Run(ctx context.Context, addr string) error {
	srv := &http.Server{
		Addr:              addr,
		Handler:           s.Handler(),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       15 * time.Second,
		IdleTimeout:       60 * time.Second,
	}

	errCh := make(chan error, 1)
	go func() { errCh <- srv.ListenAndServe() }()

	select {
	case err := <-errCh:
		return err
	case <-ctx.Done():
		shutCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		return srv.Shutdown(shutCtx)
	}
}
