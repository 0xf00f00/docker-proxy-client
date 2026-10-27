package scanner

import (
	"context"
	"errors"
	"math"
	"net"
	"net/netip"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/0xf00f00/cf-edge-manager/internal/cfst"
	edgepool "github.com/0xf00f00/cf-edge-manager/internal/pool"
	"github.com/0xf00f00/cf-edge-manager/internal/probe"
)

func (s *Service) runScan(parent context.Context, id string) {
	j, ok := s.store.Job(id)
	if !ok || j.State != StateQueued {
		return // cancelled before it started
	}
	s.markRunning(&j)
	s.log.Info("scan started", "job", id)

	ctx, cancel := context.WithTimeout(parent, s.cfg.ScanTimeout)
	s.setScanCancel(cancel)
	defer func() {
		s.clearScanCancel()
		cancel()
	}()

	if s.cfg.Preflight && !probe.AnyReachable(ctx, preflightTargets(s.cfg.Port), 3*time.Second) {
		s.markFailed(&j, errNoConnectivity)
		s.log.Warn("preflight: no upstream reachable; skipping scan, keeping previous pool", "job", id)
		return
	}
	edges, err := s.cf.Scan(ctx)
	if err != nil {
		if errors.Is(ctx.Err(), context.Canceled) {
			s.markFailed(&j, errCancelled)
			s.log.Info("scan cancelled; keeping previous pool", "job", id)
		} else {
			s.markFailed(&j, err)
			s.log.Error("scan failed; keeping previous pool", "job", id, "err", err)
		}
		return
	}

	pool := topIPs(edges, s.cfg.PoolSize)
	if len(pool) == 0 {
		s.markDone(&j)
		s.log.Warn("scan produced no candidates; keeping previous pool", "job", id)
		return
	}
	prev := len(edgepool.Read(filepath.Join(s.cfg.OutDir, "pool.txt")))
	if thinScan(prev, len(pool), s.cfg.PoolSize, s.cfg.PoolMinShare) {
		s.markDone(&j)
		s.log.Warn("scan found too few edges; keeping previous pool", "job", id, "found", len(pool), "previous", prev)
		return
	}
	if err := s.writePool(pool); err != nil {
		s.markFailed(&j, err)
		s.log.Error("scan failed writing pool", "job", id, "err", err)
		return
	}
	s.store.PutPool(pool, time.Now())
	s.markDone(&j)
	s.log.Info("scan complete", "job", id, "candidates", len(pool), "best", pool[0])
}

// thinScan reports whether a scan is too poor to replace the previous pool: one taken
// in a bad network window finds a handful of edges.
func thinScan(prev, n, poolSize int, minShare float64) bool {
	return prev > n && float64(n) < math.Ceil(float64(poolSize)*minShare)
}

func (s *Service) runTest(parent context.Context, id string) {
	j, ok := s.store.Job(id)
	if !ok {
		return
	}
	ip, err := netip.ParseAddr(j.IP)
	if err != nil {
		s.markFailed(&j, err)
		return
	}
	s.markRunning(&j)
	s.log.Info("test started", "job", id, "ip", j.IP)

	ctx, cancel := context.WithTimeout(parent, s.cfg.TestTimeout)
	defer cancel()

	stats, err := s.cf.Ping(ctx, ip)
	if err != nil {
		s.markFailed(&j, err)
		s.log.Error("test failed", "job", id, "ip", j.IP, "err", err)
		return
	}
	surv := s.runSurvival(ctx, j.IP, stats)
	s.store.PutTestResult(j.IP, stats, surv, time.Now())
	s.markDone(&j)
	s.log.Info("test complete", "job", id, "ip", j.IP, "loss", stats.Loss, "latency_ms", stats.LatencyMS,
		"survived", survivedStr(surv))
}

// runSurvival is the real-path tier of a test: only an edge cfst found reachable
// enough (loss <= KeepMax) is worth the burst. Returns nil when the tier is off.
func (s *Service) runSurvival(ctx context.Context, ip string, stats cfst.Stats) *Survival {
	if s.survival == nil {
		return nil // real-path tier not configured
	}
	if stats.Received == 0 {
		return &Survival{Skipped: "unreachable"}
	}
	if stats.Loss > s.cfg.KeepMax {
		return &Survival{Skipped: "high packet loss"}
	}
	select {
	case s.survivalSem <- struct{}{}: // one real-path probe at a time
		defer func() { <-s.survivalSem }()
	case <-ctx.Done():
		return &Survival{Skipped: "cancelled"}
	case <-time.After(survivalAcquireBudget):
		return &Survival{Skipped: "busy"}
	}
	s.log.Info("real-path probe started", "ip", ip)
	r := s.survival(ctx, ip)
	return &Survival{
		Checked:  true,
		Survived: r.Survived,
		FailRate: r.FailRate,
		Fails:    r.Fails,
		Probes:   r.Probes,
		Err:      r.Err,
	}
}

func survivedStr(s *Survival) string {
	switch {
	case s == nil:
		return "n/a"
	case !s.Checked:
		return "skipped:" + s.Skipped
	case s.Survived == nil:
		return "inconclusive"
	case *s.Survived:
		return "yes"
	default:
		return "no"
	}
}

// preflightTargets are diverse anycast addresses; the scan proceeds if any one answers, so this only trips
// on a real outage, not when specific edges are censored.
func preflightTargets(port int) []string {
	p := strconv.Itoa(port)
	return []string{
		net.JoinHostPort("1.1.1.1", p),
		net.JoinHostPort("1.0.0.1", p),
		net.JoinHostPort("8.8.8.8", p),
		net.JoinHostPort("9.9.9.9", p),
	}
}

func topIPs(edges []cfst.Edge, n int) []string {
	if len(edges) > n {
		edges = edges[:n]
	}
	out := make([]string, 0, len(edges))
	for _, e := range edges {
		out = append(out, e.IP.String())
	}
	return out
}

// writePool publishes the ranked pool atomically: pool.txt (best-first) and clean_ip.txt (the best one).
// The last-scan time is pool.txt's mtime.
func (s *Service) writePool(pool []string) error {
	if err := writeAtomic(filepath.Join(s.cfg.OutDir, "pool.txt"), strings.Join(pool, "\n")+"\n"); err != nil {
		return err
	}
	return writeAtomic(filepath.Join(s.cfg.OutDir, "clean_ip.txt"), pool[0]+"\n")
}

func writeAtomic(path, content string) error {
	dir := filepath.Dir(path)
	tmp, err := os.CreateTemp(dir, ".tmp-*")
	if err != nil {
		return err
	}
	if _, err := tmp.WriteString(content); err != nil {
		_ = tmp.Close()
		_ = os.Remove(tmp.Name())
		return err
	}
	if err := tmp.Sync(); err != nil { // flush contents before the rename
		_ = tmp.Close()
		_ = os.Remove(tmp.Name())
		return err
	}
	if err := tmp.Close(); err != nil {
		_ = os.Remove(tmp.Name())
		return err
	}
	if err := os.Rename(tmp.Name(), path); err != nil {
		return err
	}
	return fsyncDir(dir) // make the rename itself durable across power loss
}

func fsyncDir(dir string) error {
	d, err := os.Open(dir)
	if err != nil {
		return err
	}
	defer func() { _ = d.Close() }()
	return d.Sync()
}
