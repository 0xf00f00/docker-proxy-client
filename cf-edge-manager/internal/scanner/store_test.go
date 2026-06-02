package scanner

import (
	"io"
	"log/slog"
	"path/filepath"
	"testing"
	"time"

	"github.com/0xf00f00/cf-edge-manager/internal/cfst"
)

func testLogger() *slog.Logger {
	return slog.New(slog.NewTextHandler(io.Discard, nil))
}

func newTestStore(t *testing.T) Store {
	t.Helper()
	s, err := NewStore(filepath.Join(t.TempDir(), "state.json"), testLogger())
	if err != nil {
		t.Fatal(err)
	}
	return s
}

func TestReconcile(t *testing.T) {
	s := newTestStore(t)
	now := time.Now()
	started := now.Add(-time.Minute)

	s.SaveJob(Job{ID: "scan-run", Type: JobScan, State: StateRunning, CreatedAt: now, StartedAt: &started})
	s.SaveJob(Job{ID: "test-run", Type: JobTest, IP: "1.1.1.1", State: StateRunning, CreatedAt: now, StartedAt: &started})
	s.SaveJob(Job{ID: "test-q", Type: JobTest, IP: "8.8.8.8", State: StateQueued, CreatedAt: now})

	requeue := s.Reconcile()

	// Running scan -> requeued; queued test -> requeued; running test -> failed.
	if len(requeue) != 2 {
		t.Fatalf("want 2 requeued, got %d", len(requeue))
	}
	if j, _ := s.Job("scan-run"); j.State != StateQueued || j.StartedAt != nil {
		t.Errorf("scan should be reset to queued, got %+v", j)
	}
	if j, _ := s.Job("test-run"); j.State != StateFailed || j.Error == "" {
		t.Errorf("running test should be failed, got %+v", j)
	}
	if j, _ := s.Job("test-q"); j.State != StateQueued {
		t.Errorf("queued test should stay queued, got %+v", j)
	}
}

func TestActiveAndReusableTest(t *testing.T) {
	s := newTestStore(t)
	now := time.Now()

	if _, ok := s.ActiveTest("1.1.1.1"); ok {
		t.Fatal("no active test expected yet")
	}

	s.SaveJob(Job{ID: "a", Type: JobTest, IP: "1.1.1.1", State: StateQueued, CreatedAt: now})
	if j, ok := s.ActiveTest("1.1.1.1"); !ok || j.ID != "a" {
		t.Fatalf("expected active test a, got %v %v", j, ok)
	}

	fin := now
	s.SaveJob(Job{ID: "b", Type: JobTest, IP: "9.9.9.9", State: StateDone, CreatedAt: now, FinishedAt: &fin})
	if _, ok := s.ReusableTest("9.9.9.9", time.Minute); !ok {
		t.Error("recent done test should be reusable within cooldown")
	}
	if _, ok := s.ReusableTest("9.9.9.9", time.Nanosecond); ok {
		t.Error("test outside cooldown should not be reusable")
	}
}

func TestSnapshotAndPersistence(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "state.json")
	s, _ := NewStore(path, testLogger())

	at := time.Now()
	s.PutPool([]string{"104.16.0.1", "172.64.0.1"}, at)
	s.PutTestResult("1.1.1.1", cfst.Stats{Sent: 10, Received: 10, Loss: 0, LatencyMS: 12.5}, nil, at)

	snap := s.Snapshot()
	if len(snap.Pool) != 2 || snap.Pool[0] != "104.16.0.1" {
		t.Errorf("pool = %v", snap.Pool)
	}
	if snap.LastScan == nil {
		t.Error("last_scan should be set")
	}
	if r, ok := snap.Tests["1.1.1.1"]; !ok || r.LatencyMS != 12.5 {
		t.Errorf("test result = %+v ok=%v", r, ok)
	}

	// Reload from disk and confirm durability.
	s2, _ := NewStore(path, testLogger())
	snap2 := s2.Snapshot()
	if len(snap2.Pool) != 2 || snap2.Tests["1.1.1.1"].Received != 10 {
		t.Errorf("reloaded snapshot mismatch: %+v", snap2)
	}
}

func TestTestResultEviction(t *testing.T) {
	s := newTestStore(t).(*memStore)
	s.maxTests = 3
	base := time.Now()

	// Insert 4 distinct IPs oldest-first; the oldest must be evicted at the cap.
	for i, ip := range []string{"1.1.1.1", "2.2.2.2", "3.3.3.3", "4.4.4.4"} {
		s.PutTestResult(ip, cfst.Stats{Sent: 1, Received: 1}, nil, base.Add(time.Duration(i)*time.Second))
	}

	tests := s.Snapshot().Tests
	if len(tests) != 3 {
		t.Fatalf("want 3 retained, got %d", len(tests))
	}
	if _, ok := tests["1.1.1.1"]; ok {
		t.Error("oldest result (1.1.1.1) should have been evicted")
	}
	if _, ok := tests["4.4.4.4"]; !ok {
		t.Error("newest result (4.4.4.4) should be retained")
	}
}
