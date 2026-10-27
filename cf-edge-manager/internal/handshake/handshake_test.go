package handshake

import (
	"context"
	"testing"
	"time"
)

func TestCount(t *testing.T) {
	lines := []string{
		"2026/10/27 05:00:00 injector: fake ClientHello sent (1/1, 659 bytes, 1 segment(s))",
		"2026/10/27 05:00:01 injector: fake ClientHello sent (1/1, 659 bytes, 1 segment(s))",
		"2026/10/27 05:00:03 proxy: ACK timeout after 2s",
	}
	if got := count(lines); got != (Stat{Total: 2, Failed: 1}) {
		t.Errorf("count = %+v", got)
	}
}

func TestBad(t *testing.T) {
	for _, tc := range []struct {
		name string
		s    Stat
		want bool
	}{
		{"degraded edge, 23% over a real hour", Stat{255, 58}, true},
		{"clean edge with a small spike", Stat{123, 4}, false},
		{"too few attempts to say", Stat{20, 10}, false},
	} {
		if got := tc.s.Bad(0.08, 30); got != tc.want {
			t.Errorf("%s: Bad = %v, want %v", tc.name, got, tc.want)
		}
	}
}

type fakeSource struct {
	started time.Time
	since   time.Time
}

func (f *fakeSource) StartedAt(context.Context, string) (time.Time, error) { return f.started, nil }

func (f *fakeSource) Logs(_ context.Context, _ string, since time.Time) ([]string, error) {
	f.since = since
	return nil, nil
}

// Evidence from before the container restarted or the network recovered must not count.
func TestWindowStartsAfterTheLatestOfRestartAndRecovery(t *testing.T) {
	now := time.Date(2026, 10, 27, 5, 0, 0, 0, time.UTC)
	for name, tc := range map[string]struct {
		started, recovered, want time.Time
	}{
		"steady state":   {now.Add(-6 * time.Hour), time.Time{}, now.Add(-15 * time.Minute)},
		"just restarted": {now.Add(-2 * time.Minute), time.Time{}, now.Add(-2 * time.Minute)},
		"just recovered": {now.Add(-6 * time.Hour), now.Add(-4 * time.Minute), now.Add(-4 * time.Minute)},
	} {
		src := &fakeSource{started: tc.started}
		if _, err := Window(context.Background(), src, "web", now, 15*time.Minute, tc.recovered); err != nil || !src.since.Equal(tc.want) {
			t.Errorf("%s: since = %v, want %v (err %v)", name, src.since, tc.want, err)
		}
	}
}
