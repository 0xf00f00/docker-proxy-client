package scanner

import (
	"context"
	"errors"
	"net"
	"net/netip"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/0xf00f00/cf-edge-scanner/internal/cfst"
	"github.com/0xf00f00/cf-edge-scanner/internal/probe"
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
	if s.cfg.TLSCheck && len(pool) > 0 {
		pool = s.tlsGate(ctx, pool)
	}
	if len(pool) == 0 {
		s.markDone(&j)
		s.log.Warn("scan produced no candidates; keeping previous pool", "job", id)
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
	s.store.PutTestResult(j.IP, stats, time.Now())
	s.markDone(&j)
	s.log.Info("test complete", "job", id, "ip", j.IP, "loss", stats.Loss, "latency_ms", stats.LatencyMS)
}

// tlsGate filters the ranked pool to edges that carry a real TLS session past
// DPI. Sequential and spaced (≈1 KB/candidate) so it never disturbs traffic.
// If nothing survives, the TCP-ranked pool is kept as-is.
func (s *Service) tlsGate(ctx context.Context, pool []string) []string {
	s.log.Info("tls-survival gate", "candidates", len(pool), "sni", s.cfg.TLSSNI)
	var survivors []string
	for _, ipStr := range pool {
		ip, err := netip.ParseAddr(ipStr)
		if err != nil {
			continue
		}
		probeCtx, cancel := context.WithTimeout(ctx, s.cfg.TLSTimeout)
		ok, err := probe.TLSSurvives(probeCtx, ip, s.cfg.Port, s.cfg.TLSSNI, s.cfg.TLSHold)
		cancel()
		if ok {
			survivors = append(survivors, ipStr)
			s.log.Info("tls ok", "ip", ipStr)
		} else {
			s.log.Info("tls fail (dropped)", "ip", ipStr, "err", err)
		}
		select {
		case <-ctx.Done():
			return survivors
		case <-time.After(s.cfg.TLSGap):
		}
	}
	if len(survivors) == 0 {
		s.log.Warn("no candidate survived tls gate; keeping tcp-ranked pool")
		return pool
	}
	return survivors
}

// preflightTargets are diverse, widely-reachable anycast addresses (two
// Cloudflare, one Google, one Quad9). The scan proceeds if any one answers, so
// this only trips on a real outage -- not when specific edges are censored.
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

// writePool publishes the ranked pool to the files the picker consumes:
// pool.txt (all, best-first) and clean_ip.txt (the single best). The last-scan
// time is taken from pool.txt's mtime, so no separate marker is written. Writes
// are atomic and fsync'd -- this is the operational output the fallback chain
// depends on, and it changes rarely (only on a successful scan).
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
