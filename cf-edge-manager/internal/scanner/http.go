package scanner

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/netip"
	"time"
)

// eventHeartbeat re-sends the snapshot on this interval even without a change,
// so a consumer can detect a dead connection and reconnect.
const eventHeartbeat = 15 * time.Second

// Handler returns the control API. Routes use Go 1.22 method+path patterns.
func (s *Service) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /scans", s.handleScan)
	mux.HandleFunc("POST /scans/cancel", s.handleCancel)
	mux.HandleFunc("POST /tests", s.handleTest)
	mux.HandleFunc("GET /status", s.handleStatus)
	mux.HandleFunc("GET /events", s.handleEvents)
	mux.HandleFunc("GET /jobs/{id}", s.handleJob)
	mux.HandleFunc("GET /healthz", s.handleHealth)
	return mux
}

func (s *Service) handleScan(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"job_id": s.EnqueueScan()})
}

func (s *Service) handleCancel(w http.ResponseWriter, _ *http.Request) {
	s.CancelScan()
	writeJSON(w, http.StatusOK, map[string]string{"status": "cancel requested"})
}

func (s *Service) handleTest(w http.ResponseWriter, r *http.Request) {
	var req struct {
		IP string `json:"ip"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<10)).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	ip, err := netip.ParseAddr(req.IP)
	if err != nil {
		writeError(w, http.StatusBadRequest, "invalid IP address")
		return
	}
	id, err := s.EnqueueTest(ip)
	if errors.Is(err, ErrQueueFull) {
		writeError(w, http.StatusTooManyRequests, "test queue full")
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"job_id": id})
}

func (s *Service) handleStatus(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, s.store.Snapshot())
}

// handleEvents streams the status snapshot over SSE: once immediately, once per
// state change (coalesced), and once per heartbeat. The dashboard consumes this
// instead of polling.
func (s *Service) handleEvents(w http.ResponseWriter, r *http.Request) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeError(w, http.StatusInternalServerError, "streaming unsupported")
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")

	ch := s.hub.subscribe()
	defer s.hub.unsubscribe(ch)

	send := func() bool {
		data, err := json.Marshal(s.store.Snapshot())
		if err != nil {
			return false
		}
		if _, err := fmt.Fprintf(w, "event: status\ndata: %s\n\n", data); err != nil {
			return false
		}
		flusher.Flush()
		return true
	}

	if !send() {
		return
	}
	ticker := time.NewTicker(eventHeartbeat)
	defer ticker.Stop()
	for {
		select {
		case <-r.Context().Done():
			return
		case <-ch:
			if !send() {
				return
			}
		case <-ticker.C:
			if !send() {
				return
			}
		}
	}
}

func (s *Service) handleJob(w http.ResponseWriter, r *http.Request) {
	j, ok := s.store.Job(r.PathValue("id"))
	if !ok {
		writeError(w, http.StatusNotFound, "job not found")
		return
	}
	writeJSON(w, http.StatusOK, j)
}

// handleHealth is deliberately trivial: no store access, no locks. The Docker
// healthcheck (and autoheal) must not be gated on a busy scan.
func (s *Service) handleHealth(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok", "egress_ip": s.egress})
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}

func writeError(w http.ResponseWriter, code int, msg string) {
	writeJSON(w, code, map[string]string{"error": msg})
}
