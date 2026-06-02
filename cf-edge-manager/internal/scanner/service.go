// Package scanner is the stateful orchestration layer: a durable job queue with
// two independent concurrency lanes (one heavy scan, N interactive tests), a
// control API, and crash recovery. Probing itself is delegated to the cfst
// package.
package scanner

import (
	"context"
	"log/slog"
	"net"
	"os"
	"path/filepath"
	"sync"
	"time"

	"github.com/0xf00f00/cf-edge-manager/internal/cfst"
	"github.com/0xf00f00/cf-edge-manager/internal/config"
)

// scanBuffer is small: scans dedup to at most one active job.
const scanBuffer = 4

// survivalAcquireBudget bounds the wait for the single real-path slot; the probe
// can't run concurrently (one NFQUEUE, fixed ports), so a contended test degrades
// to cfst-only "busy" rather than blocking a worker.
const survivalAcquireBudget = 4 * time.Second

// SurvivalResult is the real-path verdict. Survived==nil = inconclusive (probe
// stack didn't come up), not an edge failure.
type SurvivalResult struct {
	Survived *bool
	FailRate float64
	Fails    int
	Probes   int
	Err      string
}

// SurvivalFunc runs the real-path probe for ip; nil disables the tier (cfst-only).
type SurvivalFunc func(ctx context.Context, ip string) SurvivalResult

// Service owns the scan/test worker lanes, job store, and cfst client.
type Service struct {
	cfg   config.Config
	cf    cfst.Client
	store Store
	log   *slog.Logger

	scanCh chan string
	testCh chan string
	hub    *hub

	survival    SurvivalFunc
	survivalSem chan struct{} // cap 1: serialises the real-path tier across all test workers

	egress string
	wg     sync.WaitGroup

	mu         sync.Mutex
	scanCancel context.CancelFunc // cancels the running scan, if any
}

// New constructs a Service, creating the output directory and loading the
// persisted job store.
func New(cfg config.Config, log *slog.Logger) (*Service, error) {
	if err := os.MkdirAll(cfg.OutDir, 0o755); err != nil {
		return nil, err
	}
	store, err := NewStore(filepath.Join(cfg.OutDir, "state.json"), log)
	if err != nil {
		return nil, err
	}
	return &Service{
		cfg:   cfg,
		store: store,
		log:   log,
		cf: cfst.Client{
			Bin:        cfg.CfstBin,
			RangesFile: cfg.RangesFile,
			OutDir:     cfg.OutDir,
			Threads:    cfg.Threads,
			PingCount:  cfg.PingCount,
			Port:       cfg.Port,
			LossMax:    cfg.LossMax,
			LatMax:     cfg.LatMax,
			PoolSize:   cfg.PoolSize,
			TestPings:  cfg.TestPings,
		},
		scanCh:      make(chan string, scanBuffer),
		testCh:      make(chan string, cfg.TestQueueMax),
		hub:         newHub(),
		survivalSem: make(chan struct{}, 1),
	}, nil
}

// SetSurvivalProbe installs the real-path probe hook; call once before serving.
func (s *Service) SetSurvivalProbe(fn SurvivalFunc) { s.survival = fn }

// Start launches the worker lanes and re-enqueues jobs left in flight by a
// crash. ctx governs the whole service: cancelling it stops the lanes and
// cancels any running cfst subprocess.
func (s *Service) Start(ctx context.Context) {
	s.egress = detectEgress()
	s.log.Info("egress self-check", "source_ip", s.egress)

	s.wg.Add(1)
	go s.scanLoop(ctx)
	for range s.cfg.TestConcurrency {
		s.wg.Add(1)
		go s.testLoop(ctx)
	}

	requeue := s.store.Reconcile()
	for _, j := range requeue {
		s.requeue(j)
	}
	if len(requeue) > 0 {
		s.log.Info("reconciled in-flight jobs", "count", len(requeue))
	}
}

// Wait blocks until all worker lanes have drained after ctx cancellation.
func (s *Service) Wait() { s.wg.Wait() }

// CancelScan stops the active scan: an in-flight one via its context, a
// still-queued one by marking it so the worker skips it. A no-op when idle.
func (s *Service) CancelScan() {
	s.mu.Lock()
	cancel := s.scanCancel
	s.mu.Unlock()
	if cancel != nil {
		cancel()
	}
	if j, ok := s.store.ActiveScan(); ok && j.State == StateQueued {
		s.markFailed(&j, errCancelled)
	}
}

func (s *Service) setScanCancel(c context.CancelFunc) {
	s.mu.Lock()
	s.scanCancel = c
	s.mu.Unlock()
}

func (s *Service) clearScanCancel() {
	s.mu.Lock()
	s.scanCancel = nil
	s.mu.Unlock()
}

func (s *Service) requeue(j Job) {
	ch := s.testCh
	if j.Type == JobScan {
		ch = s.scanCh
	}
	select {
	case ch <- j.ID:
	default:
		s.log.Warn("requeue dropped: lane full", "job", j.ID, "type", j.Type)
	}
}

// mark* transitions bracket the PutPool/PutTestResult calls, so notifying here
// covers every snapshot-visible change (job state, pool, test results).
func (s *Service) markRunning(j *Job) {
	now := time.Now()
	j.State = StateRunning
	j.StartedAt = &now
	s.store.SaveJob(*j)
	s.hub.notify()
}

func (s *Service) markDone(j *Job) {
	now := time.Now()
	j.State = StateDone
	j.FinishedAt = &now
	s.store.SaveJob(*j)
	s.hub.notify()
}

func (s *Service) markFailed(j *Job, err error) {
	now := time.Now()
	j.State = StateFailed
	j.Error = err.Error()
	j.FinishedAt = &now
	s.store.SaveJob(*j)
	s.hub.notify()
}

// detectEgress reports the source IP the kernel would use to reach the public
// internet. A UDP "connection" selects the route without sending any packet, so
// this is a cheap assertion that we egress via the macvlan interface, not the
// VPN default route.
func detectEgress() string {
	c, err := net.Dial("udp", "1.1.1.1:53")
	if err != nil {
		return ""
	}
	defer func() { _ = c.Close() }()
	if a, ok := c.LocalAddr().(*net.UDPAddr); ok {
		return a.IP.String()
	}
	return ""
}
