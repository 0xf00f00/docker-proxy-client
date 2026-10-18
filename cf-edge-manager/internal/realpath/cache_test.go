package realpath

import (
	"context"
	"testing"
	"time"
)

// A verdict is only valid for the decoy it was probed with: after a decoy
// switch, the old decoy's cached verdicts must not answer for the new one.
func TestCacheIsPerDecoy(t *testing.T) {
	p := New(Config{FakeSNI: "a.example", CacheTTL: time.Hour}, nil)
	ok := true
	p.cache[cacheKey{"192.0.2.1", "a.example"}] = cacheEntry{at: time.Now(), res: Result{IP: "192.0.2.1", Survived: &ok}}

	if r := p.Probe(context.Background(), "192.0.2.1", Options{}); !r.Cached {
		t.Error("default decoy must hit its cached verdict")
	}
	if r := p.Probe(context.Background(), "192.0.2.1", Options{FakeSNI: "a.example"}); !r.Cached {
		t.Error("explicit default decoy must hit its cached verdict")
	}
	if _, hit := p.cached(cacheKey{"192.0.2.1", "b.example"}); hit {
		t.Error("another decoy must not reuse the verdict")
	}
}
