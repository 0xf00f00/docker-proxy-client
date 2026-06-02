package probe

import (
	"net"
	"strconv"
	"time"
)

// TCPLoss returns the fraction of `count` raw TCP connects to ip:port that
// failed. Pure reachability (no TLS) — the cheap liveness tier: a dead edge is
// rejected here before paying for the expensive real-path survival probe. This
// is the signal the old picker used as its ONLY check; it is necessary but not
// sufficient (an edge can pass this yet reset real sessions under DPI).
func TCPLoss(ip string, port, count int, timeout time.Duration) float64 {
	if count < 1 {
		count = 1
	}
	addr := net.JoinHostPort(ip, strconv.Itoa(port))
	miss := 0
	for range count {
		c, err := net.DialTimeout("tcp", addr, timeout)
		if err != nil {
			miss++
			continue
		}
		_ = c.Close()
	}
	return float64(miss) / float64(count)
}
