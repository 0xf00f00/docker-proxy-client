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

func TestThinScan(t *testing.T) {
	for _, tc := range []struct {
		name          string
		prev, n, size int
		keep          bool
	}{
		{"healthy scan replaces", 40, 38, 40, false},
		{"a handful of edges does not replace a full pool", 40, 3, 40, true},
		{"nothing to protect on the first run", 0, 3, 40, false},
	} {
		if got := thinScan(tc.prev, tc.n, tc.size, 0.25); got != tc.keep {
			t.Errorf("%s: thinScan = %v, want %v", tc.name, got, tc.keep)
		}
	}
}
