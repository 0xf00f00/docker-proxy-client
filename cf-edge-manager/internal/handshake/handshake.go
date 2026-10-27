// Package handshake turns sni-spoofing's own connection log into a passive health signal:
// the share of proxied connections whose edge never ACKed is measured under real load,
// which brief active probes cannot see.
package handshake

import (
	"context"
	"math"
	"strings"
	"time"
)

// Log lines of sni-spoofing-go marking an attempt and a failed attempt.
const (
	attemptMark = "fake ClientHello sent"
	failMark    = "ACK timeout"
)

const z = 1.645

// Stat counts handshake attempts and how many failed.
type Stat struct{ Total, Failed int }

// Ratio is the observed failure share.
func (s Stat) Ratio() float64 {
	if s.Total == 0 {
		return 0
	}
	return float64(s.Failed) / float64(s.Total)
}

// Lower is the Wilson lower confidence bound of the failure ratio, so a few unlucky
// failures in a small sample cannot read as a bad edge.
func (s Stat) Lower() float64 {
	if s.Total == 0 {
		return 0
	}
	n, p := float64(s.Total), s.Ratio()
	centre := p + z*z/(2*n)
	margin := z * math.Sqrt(p*(1-p)/n+z*z/(4*n*n))
	return (centre - margin) / (1 + z*z/n)
}

// Bad reports whether the failure share is credibly above maxRatio, given minSamples attempts.
func (s Stat) Bad(maxRatio float64, minSamples int) bool {
	return s.Total >= minSamples && s.Lower() > maxRatio
}

func count(lines []string) Stat {
	var s Stat
	for _, ln := range lines {
		switch {
		case strings.Contains(ln, attemptMark):
			s.Total++
		case strings.Contains(ln, failMark):
			s.Failed++
		}
	}
	return s
}

// Source is where log lines come from.
type Source interface {
	StartedAt(ctx context.Context, name string) (time.Time, error)
	Logs(ctx context.Context, name string, since time.Time) ([]string, error)
}

// Window never reaches back before the container's last start (those lines describe a
// previous edge) nor before notBefore (failures while the whole network was down say
// nothing about the edge).
func Window(ctx context.Context, src Source, name string, now time.Time, window time.Duration, notBefore time.Time) (Stat, error) {
	started, err := src.StartedAt(ctx, name)
	if err != nil {
		return Stat{}, err
	}
	since := now.Add(-window)
	since = latest(since, started, notBefore)
	lines, err := src.Logs(ctx, name, since)
	if err != nil {
		return Stat{}, err
	}
	return count(lines), nil
}

func latest(ts ...time.Time) time.Time {
	out := ts[0]
	for _, t := range ts[1:] {
		if t.After(out) {
			out = t
		}
	}
	return out
}
