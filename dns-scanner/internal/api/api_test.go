package api

import (
	"bufio"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

const secret = "test-secret"

func newTestServer(t *testing.T) *httptest.Server {
	t.Helper()
	srv := NewServer(secret)
	ts := httptest.NewServer(srv.Handler())
	t.Cleanup(ts.Close)
	return ts
}

func do(t *testing.T, ts *httptest.Server, method, path, body string, withSecret bool) *http.Response {
	t.Helper()
	var r *strings.Reader
	if body != "" {
		r = strings.NewReader(body)
	} else {
		r = strings.NewReader("")
	}
	req, err := http.NewRequest(method, ts.URL+path, r)
	if err != nil {
		t.Fatal(err)
	}
	if withSecret {
		req.Header.Set("X-Scanner-Secret", secret)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	return resp
}

func TestHealthzIsUnauthenticated(t *testing.T) {
	ts := newTestServer(t)
	resp := do(t, ts, http.MethodGet, "/healthz", "", false)
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("healthz = %d, want 200", resp.StatusCode)
	}
}

func TestAuthRequiredOnScan(t *testing.T) {
	ts := newTestServer(t)
	resp := do(t, ts, http.MethodGet, "/scan", "", false)
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("GET /scan without secret = %d, want 401", resp.StatusCode)
	}
	ok := do(t, ts, http.MethodGet, "/scan", "", true)
	defer ok.Body.Close()
	if ok.StatusCode != http.StatusOK {
		t.Fatalf("GET /scan with secret = %d, want 200", ok.StatusCode)
	}
}

// Drive the control transitions directly (no HTTP) to assert idempotency and
// the start/pause/resume/stop state machine.
func TestControlStateMachine(t *testing.T) {
	s := NewServer("")

	if got, _ := s.pause(); got != http.StatusConflict {
		t.Fatalf("pause while idle = %d, want 409", got)
	}
	if got, _ := s.resume(); got != http.StatusConflict {
		t.Fatalf("resume while idle = %d, want 409", got)
	}
	if got, _ := s.stop(); got != http.StatusOK {
		t.Fatalf("stop while idle = %d, want 200 (no-op)", got)
	}

	// Start: idle -> trigger (202), and the trigger channel fires once.
	if got, _ := s.start(); got != http.StatusAccepted {
		t.Fatalf("start = %d, want 202", got)
	}
	select {
	case <-s.Trigger():
	default:
		t.Fatal("start did not fire the trigger")
	}

	// Simulate the scan loop picking it up.
	ctx, cancel := context.WithCancel(context.Background())
	s.BeginRun(time.Now().Unix(), cancel)
	if s.Get().State != StateScanning {
		t.Fatalf("state after BeginRun = %q, want scanning", s.Get().State)
	}

	// Starting again while scanning is an idempotent no-op.
	if got, _ := s.start(); got != http.StatusOK {
		t.Fatalf("start while scanning = %d, want 200", got)
	}

	// Pause, then pause again (idempotent).
	if got, _ := s.pause(); got != http.StatusOK {
		t.Fatalf("pause = %d, want 200", got)
	}
	if s.Get().State != StatePaused {
		t.Fatalf("state after pause = %q, want paused", s.Get().State)
	}
	if got, _ := s.pause(); got != http.StatusOK {
		t.Fatalf("pause again = %d, want 200 (idempotent)", got)
	}

	// Start while paused is rejected — resume is the correct verb.
	if got, _ := s.start(); got != http.StatusConflict {
		t.Fatalf("start while paused = %d, want 409", got)
	}

	if got, _ := s.resume(); got != http.StatusOK {
		t.Fatalf("resume = %d, want 200", got)
	}
	if s.Get().State != StateScanning {
		t.Fatalf("state after resume = %q, want scanning", s.Get().State)
	}

	// Stop cancels the run's context.
	if got, _ := s.stop(); got != http.StatusOK {
		t.Fatalf("stop = %d, want 200", got)
	}
	if s.Get().State != StateStopping {
		t.Fatalf("state after stop = %q, want stopping", s.Get().State)
	}
	select {
	case <-ctx.Done():
	default:
		t.Fatal("stop did not cancel the run context")
	}

	// The loop finishes the run.
	s.EndRun(time.Now().Unix(), 1, "stopped", nil, 0)
	if s.Get().State != StateIdle {
		t.Fatalf("state after EndRun = %q, want idle", s.Get().State)
	}
}

// SetTarget surfaces the per-cycle target in the snapshot (drives the
// dashboard's "accepted of target" progress).
func TestSetTarget(t *testing.T) {
	s := NewServer("")
	if got := s.Get().TargetN; got != 0 {
		t.Fatalf("initial TargetN = %d, want 0", got)
	}
	s.SetTarget(15)
	if got := s.Get().TargetN; got != 15 {
		t.Fatalf("TargetN after SetTarget = %d, want 15", got)
	}
}

// The action endpoints are wired and gated by auth.
func TestActionEndpoints(t *testing.T) {
	ts := newTestServer(t)
	if resp := do(t, ts, http.MethodPost, "/scan/start", "", false); resp.StatusCode != http.StatusUnauthorized {
		resp.Body.Close()
		t.Fatalf("start without secret = %d, want 401", resp.StatusCode)
	}
	resp := do(t, ts, http.MethodPost, "/scan/start", "", true)
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusAccepted {
		t.Fatalf("start = %d, want 202", resp.StatusCode)
	}
	// Wrong verb on an action path is a 405 from the mux.
	bad := do(t, ts, http.MethodGet, "/scan/start", "", true)
	defer bad.Body.Close()
	if bad.StatusCode != http.StatusMethodNotAllowed {
		t.Fatalf("GET /scan/start = %d, want 405", bad.StatusCode)
	}
}

// Wait blocks while paused and releases when the run resumes.
func TestWaitGateReleasesOnResume(t *testing.T) {
	s := NewServer("")
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	s.BeginRun(time.Now().Unix(), cancel)
	s.pause()

	released := make(chan error, 1)
	go func() { released <- s.Wait(ctx) }()

	select {
	case <-released:
		t.Fatal("Wait returned while paused")
	case <-time.After(50 * time.Millisecond):
	}

	s.resume()
	select {
	case err := <-released:
		if err != nil {
			t.Fatalf("Wait err after resume = %v, want nil", err)
		}
	case <-time.After(time.Second):
		t.Fatal("Wait did not release after resume")
	}
}

// Wait releases (with the context error) when a paused run is stopped.
func TestWaitGateReleasesOnStop(t *testing.T) {
	s := NewServer("")
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	s.BeginRun(time.Now().Unix(), cancel)
	s.pause()

	released := make(chan error, 1)
	go func() { released <- s.Wait(ctx) }()

	s.stop()
	select {
	case err := <-released:
		if err == nil {
			t.Fatal("Wait err after stop = nil, want context cancelled")
		}
	case <-time.After(time.Second):
		t.Fatal("Wait did not release after stop")
	}
}

func TestSSEStreamsSnapshot(t *testing.T) {
	srv := NewServer("")
	ts := httptest.NewServer(srv.Handler())
	defer ts.Close()

	resp, err := http.Get(ts.URL + "/scan/events") // open server, no secret
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if ct := resp.Header.Get("Content-Type"); ct != "text/event-stream" {
		t.Fatalf("content-type = %q, want text/event-stream", ct)
	}

	// First event is the primed current snapshot.
	got := readSSEData(t, resp)
	var snap Snapshot
	if err := json.Unmarshal([]byte(got), &snap); err != nil {
		t.Fatalf("primed snapshot not JSON: %v (%q)", err, got)
	}
	if snap.State != StateIdle {
		t.Fatalf("primed state = %q, want idle", snap.State)
	}

	// A state change is pushed live.
	go func() {
		time.Sleep(20 * time.Millisecond)
		srv.BeginRun(time.Now().Unix(), func() {})
	}()
	got = readSSEData(t, resp)
	if err := json.Unmarshal([]byte(got), &snap); err != nil {
		t.Fatalf("pushed snapshot not JSON: %v (%q)", err, got)
	}
	if snap.State != StateScanning {
		t.Fatalf("pushed state = %q, want scanning", snap.State)
	}
}

// readSSEData reads lines until the next `data:` payload, skipping comments.
func readSSEData(t *testing.T, resp *http.Response) string {
	t.Helper()
	sc := bufio.NewScanner(resp.Body)
	deadline := time.Now().Add(2 * time.Second)
	for sc.Scan() {
		if time.Now().After(deadline) {
			t.Fatal("timed out reading SSE data")
		}
		line := sc.Text()
		if strings.HasPrefix(line, "data: ") {
			return strings.TrimPrefix(line, "data: ")
		}
	}
	t.Fatal("SSE stream ended before a data line")
	return ""
}
