package probe

import (
	"net"
	"strconv"
	"sync"
	"sync/atomic"
	"time"
)

const lossWorkers = 6

// TCPLoss returns the fraction of `count` raw TCP connects to ip:port that
// failed on their first attempt. timeout must stay under the kernel's 1s SYN
// retransmit: with a longer one a retry rescues a lost SYN, and a path losing 57% of
// packets reads as ~32% connect failures.
func TCPLoss(ip string, port, count int, timeout time.Duration) float64 {
	if count < 1 {
		count = 1
	}
	addr := net.JoinHostPort(ip, strconv.Itoa(port))
	var miss atomic.Int32
	jobs := make(chan struct{})
	var wg sync.WaitGroup
	for range min(lossWorkers, count) {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for range jobs {
				c, err := net.DialTimeout("tcp", addr, timeout)
				if err != nil {
					miss.Add(1)
					continue
				}
				_ = c.Close()
			}
		}()
	}
	for range count {
		jobs <- struct{}{}
	}
	close(jobs)
	wg.Wait()
	return float64(miss.Load()) / float64(count)
}
