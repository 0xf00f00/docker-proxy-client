package scanner

import (
	"context"
	"errors"
	"net/netip"
	"time"
)

// ErrQueueFull is returned when the bounded test lane is saturated.
var ErrQueueFull = errors.New("queue full")

// errCancelled marks a scan stopped on request (vs. a genuine failure).
var errCancelled = errors.New("cancelled")

// errNoConnectivity marks a scan skipped because preflight found no upstream
// reachable at all (a total outage). The previous pool is kept.
var errNoConnectivity = errors.New("no upstream connectivity")

// EnqueueScan schedules a full scan. Concurrent requests coalesce onto the
// single active scan, so the API is idempotent under retries.
func (s *Service) EnqueueScan() string {
	if j, ok := s.store.ActiveScan(); ok {
		return j.ID
	}
	j := Job{ID: newID(), Type: JobScan, State: StateQueued, CreatedAt: time.Now()}
	s.store.SaveJob(j)
	s.hub.notify()
	select {
	case s.scanCh <- j.ID:
	default:
		s.markFailed(&j, ErrQueueFull)
	}
	return j.ID
}

// EnqueueTest schedules an interactive probe of ip. A fresh prior result is
// reused (cooldown), an in-flight probe of the same IP is coalesced, and the
// lane is bounded — so an unauthenticated caller cannot flood the worker.
func (s *Service) EnqueueTest(ip netip.Addr) (string, error) {
	canon := ip.String()
	if j, ok := s.store.ReusableTest(canon, s.cfg.TestCooldown); ok {
		return j.ID, nil
	}
	if j, ok := s.store.ActiveTest(canon); ok {
		return j.ID, nil
	}
	if s.store.PendingTests() >= s.cfg.TestQueueMax {
		return "", ErrQueueFull
	}
	j := Job{ID: newID(), Type: JobTest, IP: canon, State: StateQueued, CreatedAt: time.Now()}
	s.store.SaveJob(j)
	s.hub.notify()
	select {
	case s.testCh <- j.ID:
		return j.ID, nil
	default:
		s.markFailed(&j, ErrQueueFull)
		return "", ErrQueueFull
	}
}

func (s *Service) scanLoop(ctx context.Context) {
	defer s.wg.Done()
	for {
		select {
		case <-ctx.Done():
			return
		case id := <-s.scanCh:
			s.runScan(ctx, id)
		}
	}
}

func (s *Service) testLoop(ctx context.Context) {
	defer s.wg.Done()
	for {
		select {
		case <-ctx.Done():
			return
		case id := <-s.testCh:
			s.runTest(ctx, id)
		}
	}
}
