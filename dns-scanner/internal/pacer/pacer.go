// Package pacer enforces politeness: a global rate limiter plus a per-/24
// cooldown so adjacent IPs in a subnet are never probed back-to-back (defeats
// the scanner wire signature). Adaptive yield-to-traffic comes in a later phase.
package pacer

import (
	"context"
	"net"
	"sync"
	"time"
)

type Pacer struct {
	tokens   chan struct{}
	done     chan struct{}
	cooldown time.Duration

	mu     sync.Mutex
	last24 map[string]time.Time
}

// New starts a refill loop emitting ~qps tokens/sec and applies the per-/24
// cooldown. Call Close to stop the refill goroutine.
func New(qps int, cooldown time.Duration) *Pacer {
	if qps < 1 {
		qps = 1
	}
	p := &Pacer{
		tokens:   make(chan struct{}, 1),
		done:     make(chan struct{}),
		cooldown: cooldown,
		last24:   make(map[string]time.Time),
	}
	interval := time.Second / time.Duration(qps)
	if interval <= 0 {
		interval = time.Millisecond
	}
	go func() {
		t := time.NewTicker(interval)
		defer t.Stop()
		for {
			select {
			case <-p.done:
				return
			case <-t.C:
				select {
				case p.tokens <- struct{}{}:
				default:
				}
			}
		}
	}()
	return p
}

// Close stops the refill goroutine. Acquire calls after Close will block on the
// token channel until their context is done; callers stop feeding work first.
func (p *Pacer) Close() { close(p.done) }

// Acquire blocks until ip's /24 is off cooldown and a rate token is available,
// or ctx is done.
func (p *Pacer) Acquire(ctx context.Context, ip string) error {
	key := slash24(ip)

	p.mu.Lock()
	if last, ok := p.last24[key]; ok {
		if wait := p.cooldown - time.Since(last); wait > 0 {
			p.mu.Unlock()
			select {
			case <-time.After(wait):
			case <-ctx.Done():
				return ctx.Err()
			}
			p.mu.Lock()
		}
	}
	p.last24[key] = time.Now()
	p.mu.Unlock()

	select {
	case <-p.tokens:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func slash24(ip string) string {
	parsed := net.ParseIP(ip).To4()
	if parsed == nil {
		return ip
	}
	parsed[3] = 0
	return parsed.String()
}
