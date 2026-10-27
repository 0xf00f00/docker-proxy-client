package probe

import (
	"context"
	"net"
	"time"
)

// AnyReachable reports whether at least one target accepts a TCP connection within timeout; targets are
// dialled concurrently. A lenient preflight: partial censorship never suppresses a scan, only a full outage.
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
