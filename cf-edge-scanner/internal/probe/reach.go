package probe

import (
	"context"
	"net"
	"time"
)

// AnyReachable reports whether at least one target accepts a TCP connection
// within timeout. Used as a lenient preflight: a single success is enough to
// proceed, so partial censorship (where some edges are blocked) never suppresses
// a scan -- only a genuine outage, where every diverse target fails, does.
//
// Targets are dialled concurrently, so the whole check resolves within one
// timeout window rather than the sum across targets.
func AnyReachable(ctx context.Context, targets []string, timeout time.Duration) bool {
	if len(targets) == 0 {
		return false
	}
	ctx, cancel := context.WithCancel(ctx)
	defer cancel() // a success cancels the remaining in-flight dials

	results := make(chan bool, len(targets))
	var d net.Dialer
	for _, target := range targets {
		go func(t string) {
			dialCtx, c := context.WithTimeout(ctx, timeout)
			defer c()
			conn, err := d.DialContext(dialCtx, "tcp", t)
			if err == nil {
				_ = conn.Close()
				results <- true
				return
			}
			results <- false
		}(target)
	}
	for range targets {
		if <-results {
			return true
		}
	}
	return false
}
