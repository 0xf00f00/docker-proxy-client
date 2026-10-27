package manager

import (
	"sync"
	"time"
)

type memo[T any] struct {
	mu  sync.Mutex
	at  time.Time
	val T
	ok  bool
}

func (c *memo[T]) get(ttl time.Duration, fn func() T) T {
	c.mu.Lock()
	defer c.mu.Unlock()
	if !c.ok || time.Since(c.at) > ttl {
		c.val, c.at, c.ok = fn(), time.Now(), true
	}
	return c.val
}
