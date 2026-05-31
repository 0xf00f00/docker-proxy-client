// Package cfst is a thin, stateless wrapper around the CloudflareSpeedTest
// (cfst) binary. Each call runs cfst as an isolated subprocess, so a long scan
// and short single-IP tests run concurrently without sharing cfst's
// package-level global state.
package cfst

import (
	"bytes"
	"context"
	"fmt"
	"net/netip"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"syscall"
	"time"
)

// Client holds the cfst invocation parameters. It is safe for concurrent use:
// every method spawns its own process and writes to its own output file.
type Client struct {
	Bin        string
	RangesFile string
	OutDir     string

	Threads   int
	PingCount int
	Port      int
	LossMax   float64
	LatMax    int
	PoolSize  int
	TestPings int
}

// Scan ranks the configured CIDR ranges and returns the top edges, best-first.
// An empty result (no row survived the loss/latency filters) is returned as an
// empty slice with a nil error — the caller decides whether to keep a prior pool.
func (c Client) Scan(ctx context.Context) ([]Edge, error) {
	cidrFile, cleanup, err := c.writeCIDRFile()
	if err != nil {
		return nil, err
	}
	defer cleanup()

	out := filepath.Join(c.OutDir, "result.csv")
	args := []string{
		"-f", cidrFile,
		"-o", out,
		"-dd", // skip the bandwidth-heavy download phase
		"-n", strconv.Itoa(c.Threads),
		"-t", strconv.Itoa(c.PingCount),
		"-tp", strconv.Itoa(c.Port),
		"-tlr", strconv.FormatFloat(c.LossMax, 'f', -1, 64),
		"-tl", strconv.Itoa(c.LatMax),
		"-p", strconv.Itoa(c.PoolSize),
	}
	if err := c.run(ctx, out, args); err != nil {
		return nil, err
	}
	return readEdges(out)
}

// Ping measures a single edge. ip must already be validated/canonical. When
// cfst produces no row (host unreachable or timed out) the bash-era "fully
// lost" sentinel is returned so the dashboard renders it identically.
func (c Client) Ping(ctx context.Context, ip netip.Addr) (Stats, error) {
	out, err := os.CreateTemp(c.OutDir, "test-*.csv")
	if err != nil {
		return Stats{}, err
	}
	outPath := out.Name()
	_ = out.Close()
	defer func() { _ = os.Remove(outPath) }()

	args := []string{
		"-ip", ip.String(),
		"-o", outPath,
		"-dd",
		"-t", strconv.Itoa(c.TestPings),
		"-tlr", "1",
		"-tl", "9999",
		"-p", "1",
	}
	// cfst exits non-zero when nothing passes its (here permissive) filters; that
	// is the unreachable case, not a runner error, so we ignore the exit status.
	_ = c.run(ctx, outPath, args)

	edges, err := readEdges(outPath)
	if err != nil {
		return Stats{}, err
	}
	for _, e := range edges {
		if e.IP == ip {
			return e.Stats, nil
		}
	}
	return Stats{Sent: c.TestPings, Received: 0, Loss: 1, LatencyMS: 0}, nil
}

func (c Client) run(ctx context.Context, outPath string, args []string) error {
	// Start from a clean output file so a stale CSV is never mis-parsed as a result.
	_ = os.Remove(outPath)

	cmd := exec.CommandContext(ctx, c.Bin, args...)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	cmd.Cancel = func() error {
		// Negative PID signals the whole process group, reaping any descendants.
		return syscall.Kill(-cmd.Process.Pid, syscall.SIGTERM)
	}
	cmd.WaitDelay = 5 * time.Second

	var stderr bytes.Buffer
	cmd.Stderr = &tailWriter{buf: &stderr, max: 2048}

	if err := cmd.Run(); err != nil {
		return fmt.Errorf("cfst %v: %w: %s", args, err, stderr.String())
	}
	return nil
}

func readEdges(path string) ([]Edge, error) {
	f, err := os.Open(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil // cfst wrote nothing; treat as empty
		}
		return nil, err
	}
	defer func() { _ = f.Close() }()
	return parseEdges(f)
}

// writeCIDRFile distils the documented ranges file (which may carry comments)
// into the CIDR-only form cfst's -f flag requires.
func (c Client) writeCIDRFile() (path string, cleanup func(), err error) {
	cidrs, err := readCIDRs(c.RangesFile)
	if err != nil {
		return "", nil, err
	}
	if len(cidrs) == 0 {
		return "", nil, fmt.Errorf("no valid CIDRs in %s", c.RangesFile)
	}
	f, err := os.CreateTemp("", "cf-ranges-*.txt")
	if err != nil {
		return "", nil, err
	}
	for _, cidr := range cidrs {
		if _, err := fmt.Fprintln(f, cidr); err != nil {
			_ = f.Close()
			_ = os.Remove(f.Name())
			return "", nil, err
		}
	}
	if err := f.Close(); err != nil {
		_ = os.Remove(f.Name())
		return "", nil, err
	}
	return f.Name(), func() { _ = os.Remove(f.Name()) }, nil
}

// tailWriter keeps only the last max bytes written, bounding stderr capture.
type tailWriter struct {
	buf *bytes.Buffer
	max int
}

func (w *tailWriter) Write(p []byte) (int, error) {
	n := len(p)
	w.buf.Write(p)
	if w.buf.Len() > w.max {
		over := w.buf.Len() - w.max
		w.buf.Next(over)
	}
	return n, nil
}
