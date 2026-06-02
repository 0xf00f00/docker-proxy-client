package scanner

import (
	"context"
	"errors"
	"net"
	"net/netip"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/0xf00f00/cf-edge-manager/internal/cfst"
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
	s.checkEgress()

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

// tlsGate re-orders the TCP-ranked pool by fake-SNI connect-survival: each
// candidate gets a concurrent burst of TLSBurst sessions, candidates failing
// more than TLSFailMax of the burst are dropped, and the survivors are returned
// cleanest-first. A raw TCP ping (and a lone TLS handshake) both pass an edge
// that resets 1-in-7 connections under concurrency; this gate is what tells a
// 0%-loss edge from a 15%-loss one, so the picker — which takes the pool
// best-first — inherits a pool ordered by what actually breaks in real use.
//
// One candidate at a time, spaced by TLSGap, so the burst never overlaps the
// next candidate's and the whole gate stays a light, non-disruptive trickle. If
// nothing survives the gate, the TCP-ranked pool is kept as-is rather than
// emptied (a network-wide bad window must not wipe the pool).
func (s *Service) tlsGate(ctx context.Context, pool []string) []string {
	s.log.Info("connect-survival gate", "candidates", len(pool), "sni", s.cfg.TLSSNI,
		"burst", s.cfg.TLSBurst, "fail_max", s.cfg.TLSFailMax)
	type ranked struct {
		ip       string
		failRate float64
	}
	var survivors []ranked
gate:
	for _, ipStr := range pool {
		ip, err := netip.ParseAddr(ipStr)
		if err != nil {
			continue
		}
		probeCtx, cancel := context.WithTimeout(ctx, s.cfg.TLSTimeout)
		failRate, sample := probe.BurstSurvival(probeCtx, ip, s.cfg.Port, s.cfg.TLSSNI, s.cfg.TLSUTLS, s.cfg.TLSHold, s.cfg.TLSBurst)
		cancel()
		if failRate <= s.cfg.TLSFailMax {
			survivors = append(survivors, ranked{ipStr, failRate})
			s.log.Info("survival ok", "ip", ipStr, "fail_rate", failRate)
		} else {
			s.log.Info("survival fail (dropped)", "ip", ipStr, "fail_rate", failRate, "err", sample)
		}
		select {
		case <-ctx.Done():
			break gate // scan deadline hit; keep whatever survived so far
		case <-time.After(s.cfg.TLSGap):
		}
	}
	if len(survivors) == 0 {
		s.log.Warn("no candidate survived connect-survival gate; keeping tcp-ranked pool")
		return pool
	}
	// Stable sort by failure rate keeps the cfst latency order among ties, so the
	// best-first pool is "cleanest first, then fastest".
	sort.SliceStable(survivors, func(i, j int) bool { return survivors[i].failRate < survivors[j].failRate })
	out := make([]string, len(survivors))
	for i, r := range survivors {
		out[i] = r.ip
	}
	return out
}

// checkEgress warns (non-fatally) if the scanner would dial out on an address
// outside ExpectEgressPrefix — a guard that probes leave via the macvlan eth0 IP
// and not the VPN default route, which would measure the wrong path. Disabled
// when the prefix is unset; a malformed prefix or failed lookup is logged, not
// fatal, since a wrong guard must never stop a scan.
func (s *Service) checkEgress() {
	if s.cfg.ExpectEgressPrefix == "" {
		return
	}
	want, err := netip.ParsePrefix(s.cfg.ExpectEgressPrefix)
	if err != nil {
		s.log.Warn("egress check: bad SCAN_EGRESS_PREFIX, skipping", "prefix", s.cfg.ExpectEgressPrefix, "err", err)
		return
	}
	src := probe.DefaultInterfaceIPv4("1.1.1.1")
	addr, err := netip.ParseAddr(src)
	if err != nil {
		s.log.Warn("egress check: could not determine source IP, skipping", "got", src)
		return
	}
	if !want.Contains(addr) {
		s.log.Warn("egress NOT on expected interface; probes may be leaking onto the VPN route",
			"egress_ip", src, "expected", s.cfg.ExpectEgressPrefix)
	}
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
