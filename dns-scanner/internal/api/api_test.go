package api

import (
	"context"
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
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("healthz = %d, want 200", resp.StatusCode)
	}
}

func TestAuthRequiredOnScan(t *testing.T) {
	ts := newTestServer(t)
	resp := do(t, ts, http.MethodGet, "/scan", "", false)
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("GET /scan without secret = %d, want 401", resp.StatusCode)
	}
	ok := do(t, ts, http.MethodGet, "/scan", "", true)
	defer func() { _ = ok.Body.Close() }()
	if ok.StatusCode != http.StatusOK {
		t.Fatalf("GET /scan with secret = %d, want 200", ok.StatusCode)
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
