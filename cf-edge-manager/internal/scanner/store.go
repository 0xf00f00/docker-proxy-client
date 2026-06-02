package scanner

import (
	"encoding/json"
	"log/slog"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"

	"github.com/0xf00f00/cf-edge-manager/internal/cfst"
)

// TestResult is the latest measurement for one IP. cfst.Stats is embedded so
// its fields (sent/received/loss/latency_ms) promote into the JSON object the
// dashboard renders. Survival is the real-path verdict, nil for cfst-only.
type TestResult struct {
	cfst.Stats
	At       time.Time `json:"ts"`
	Survival *Survival `json:"survival,omitempty"`
}

// Survival is the real-path verdict. Survived==nil = inconclusive (probe stack
// didn't come up), not a failure. Checked==false means it didn't run; Skipped
// says why (gate, busy, or not configured).
type Survival struct {
	Checked  bool    `json:"checked"`
	Survived *bool   `json:"survived,omitempty"`
	FailRate float64 `json:"fail_rate"`
	Fails    int     `json:"fails"`
	Probes   int     `json:"probes"`
	Skipped  string  `json:"skipped,omitempty"`
	Err      string  `json:"error,omitempty"`
}

// Snapshot is the live view served by GET /status.
type Snapshot struct {
	Scanning    bool                  `json:"scanning"`
	TestingIP   string                `json:"testing_ip,omitempty"`
	TestPending bool                  `json:"test_pending"`
	LastScan    *time.Time            `json:"last_scan"`
	Pool        []string              `json:"pool"`
	Tests       map[string]TestResult `json:"tests"`
}

// Store persists scanner state and answers the queries the worker and API need.
// The in-memory implementation is the durable source of truth for job/test
// history; the ranked pool is additionally written to pool.txt for the picker.
// An interface keeps a future SQLite backend a drop-in swap.
type Store interface {
	SaveJob(j Job)
	Job(id string) (Job, bool)
	RecentJobs() []Job
	ActiveScan() (Job, bool)
	ActiveTest(ip string) (Job, bool)
	ReusableTest(ip string, cooldown time.Duration) (Job, bool)
	PendingTests() int
	PutPool(ips []string, at time.Time)
	PutTestResult(ip string, s cfst.Stats, surv *Survival, at time.Time)
	Snapshot() Snapshot
	// Reconcile repairs jobs left mid-flight by a crash and returns the jobs to
	// re-enqueue (queued jobs, plus scans reset from running).
	Reconcile() []Job
}

const (
	defaultMaxJobs  = 200
	defaultMaxTests = 512 // cap distinct per-IP results so memory can't grow unbounded
)

type memStore struct {
	mu       sync.RWMutex
	path     string
	log      *slog.Logger
	maxJobs  int
	maxTests int
	jobs     map[string]Job
	order    []string // job IDs, insertion order (for pruning)
	tests    map[string]TestResult
	pool     []string
	lastScan time.Time
}

// NewStore loads any persisted state from path (missing/corrupt is treated as
// empty) and returns a ready store.
func NewStore(path string, log *slog.Logger) (Store, error) {
	s := &memStore{
		path:     path,
		log:      log,
		maxJobs:  defaultMaxJobs,
		maxTests: defaultMaxTests,
		jobs:     map[string]Job{},
		tests:    map[string]TestResult{},
	}
	s.load()
	return s, nil
}

type persisted struct {
	Jobs     []Job                 `json:"jobs"`
	Tests    map[string]TestResult `json:"tests"`
	Pool     []string              `json:"pool"`
	LastScan time.Time             `json:"last_scan"`
}

func (s *memStore) load() {
	data, err := os.ReadFile(s.path)
	if err != nil {
		if !os.IsNotExist(err) {
			s.log.Warn("state read failed; starting empty", "path", s.path, "err", err)
		}
		return
	}
	var p persisted
	if err := json.Unmarshal(data, &p); err != nil {
		s.log.Warn("state corrupt; starting empty", "path", s.path, "err", err)
		return
	}
	for _, j := range p.Jobs {
		s.jobs[j.ID] = j
		s.order = append(s.order, j.ID)
	}
	if p.Tests != nil {
		s.tests = p.Tests
	}
	s.pool = p.Pool
	s.lastScan = p.LastScan
}

// persist writes the whole (small) state atomically. Callers hold s.mu. A
// failure (e.g. a full disk) is logged but non-fatal: the service keeps running
// on its in-memory state, and pool.txt -- the artifact the picker needs -- is
// written separately.
func (s *memStore) persist() {
	if err := s.writeState(); err != nil {
		s.log.Warn("state persist failed", "path", s.path, "err", err)
	}
}

func (s *memStore) writeState() error {
	jobs := make([]Job, 0, len(s.order))
	for _, id := range s.order {
		jobs = append(jobs, s.jobs[id])
	}
	data, err := json.Marshal(persisted{
		Jobs:     jobs,
		Tests:    s.tests,
		Pool:     s.pool,
		LastScan: s.lastScan,
	})
	if err != nil {
		return err
	}
	tmp, err := os.CreateTemp(filepath.Dir(s.path), ".state-*.tmp")
	if err != nil {
		return err
	}
	if _, err := tmp.Write(data); err != nil {
		_ = tmp.Close()
		_ = os.Remove(tmp.Name())
		return err
	}
	if err := tmp.Close(); err != nil {
		_ = os.Remove(tmp.Name())
		return err
	}
	return os.Rename(tmp.Name(), s.path)
}

func (s *memStore) SaveJob(j Job) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, exists := s.jobs[j.ID]; !exists {
		s.order = append(s.order, j.ID)
	}
	s.jobs[j.ID] = j
	s.prune()
	s.persist()
}

func (s *memStore) prune() {
	for len(s.order) > s.maxJobs {
		id := s.order[0]
		if !s.jobs[id].State.terminal() {
			break // never drop an in-flight job
		}
		delete(s.jobs, id)
		s.order = s.order[1:]
	}
}

func (s *memStore) Job(id string) (Job, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	j, ok := s.jobs[id]
	return j, ok
}

func (s *memStore) RecentJobs() []Job {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make([]Job, 0, len(s.order))
	for _, id := range s.order {
		out = append(out, s.jobs[id])
	}
	return out
}

func (s *memStore) ActiveScan() (Job, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	for i := len(s.order) - 1; i >= 0; i-- {
		j := s.jobs[s.order[i]]
		if j.Type == JobScan && (j.State == StateQueued || j.State == StateRunning) {
			return j, true
		}
	}
	return Job{}, false
}

func (s *memStore) ActiveTest(ip string) (Job, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	for i := len(s.order) - 1; i >= 0; i-- {
		j := s.jobs[s.order[i]]
		if j.Type == JobTest && j.IP == ip && (j.State == StateQueued || j.State == StateRunning) {
			return j, true
		}
	}
	return Job{}, false
}

func (s *memStore) ReusableTest(ip string, cooldown time.Duration) (Job, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	for i := len(s.order) - 1; i >= 0; i-- {
		j := s.jobs[s.order[i]]
		if j.Type == JobTest && j.IP == ip && j.State == StateDone && j.FinishedAt != nil {
			if time.Since(*j.FinishedAt) < cooldown {
				return j, true
			}
			return Job{}, false
		}
	}
	return Job{}, false
}

func (s *memStore) PendingTests() int {
	s.mu.RLock()
	defer s.mu.RUnlock()
	n := 0
	for _, id := range s.order {
		if j := s.jobs[id]; j.Type == JobTest && j.State == StateQueued {
			n++
		}
	}
	return n
}

func (s *memStore) PutPool(ips []string, at time.Time) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.pool = append([]string(nil), ips...)
	s.lastScan = at
	s.persist()
}

func (s *memStore) PutTestResult(ip string, st cfst.Stats, surv *Survival, at time.Time) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, exists := s.tests[ip]; !exists && len(s.tests) >= s.maxTests {
		s.evictOldestTest()
	}
	s.tests[ip] = TestResult{Stats: st, At: at, Survival: surv}
	s.persist()
}

// evictOldestTest drops the least-recently-measured IP. Callers hold s.mu.
func (s *memStore) evictOldestTest() {
	var oldestIP string
	var oldestAt time.Time
	for ip, r := range s.tests {
		if oldestIP == "" || r.At.Before(oldestAt) {
			oldestIP, oldestAt = ip, r.At
		}
	}
	if oldestIP != "" {
		delete(s.tests, oldestIP)
	}
}

func (s *memStore) Snapshot() Snapshot {
	s.mu.RLock()
	defer s.mu.RUnlock()
	snap := Snapshot{
		Pool:  append([]string(nil), s.pool...),
		Tests: make(map[string]TestResult, len(s.tests)),
	}
	if !s.lastScan.IsZero() {
		t := s.lastScan
		snap.LastScan = &t
	}
	for ip, r := range s.tests {
		snap.Tests[ip] = r
	}
	for _, id := range s.order {
		j := s.jobs[id]
		switch {
		case j.Type == JobScan && j.State == StateRunning:
			snap.Scanning = true
		case j.Type == JobTest && j.State == StateRunning && snap.TestingIP == "":
			snap.TestingIP = j.IP
		case j.Type == JobTest && j.State == StateQueued:
			snap.TestPending = true
		}
	}
	return snap
}

func (s *memStore) Reconcile() []Job {
	s.mu.Lock()
	defer s.mu.Unlock()
	now := time.Now()
	var requeue []Job
	for _, id := range s.order {
		j := s.jobs[id]
		switch {
		case j.State == StateQueued:
			requeue = append(requeue, j)
		case j.State == StateRunning && j.Type == JobScan:
			j.State = StateQueued
			j.StartedAt = nil
			s.jobs[id] = j
			requeue = append(requeue, j)
		case j.State == StateRunning && j.Type == JobTest:
			j.State = StateFailed
			j.Error = "interrupted by restart"
			j.FinishedAt = &now
			s.jobs[id] = j
		}
	}
	// Sort requeued scans before tests is unnecessary; order is preserved by the
	// caller's enqueue. Keep deterministic by creation time.
	sort.SliceStable(requeue, func(a, b int) bool {
		return requeue[a].CreatedAt.Before(requeue[b].CreatedAt)
	})
	s.persist()
	return requeue
}
