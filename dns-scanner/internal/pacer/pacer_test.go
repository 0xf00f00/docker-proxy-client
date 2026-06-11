package pacer

import (
	"context"
	"testing"
	"time"
)

func TestSlash24(t *testing.T) {
	cases := map[string]string{
		"192.168.1.42": "192.168.1.0",
		"8.8.8.8":      "8.8.8.0",
		"not-an-ip":    "not-an-ip", // passes through unchanged
	}
	for in, want := range cases {
		if got := slash24(in); got != want {
			t.Errorf("slash24(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestAcquireHonoursCancelledContext(t *testing.T) {
	p := New(1, 10*time.Millisecond)
	defer p.Close()
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := p.Acquire(ctx, "1.2.3.4"); err == nil {
		t.Error("Acquire with cancelled context = nil, want ctx error")
	}
}

func TestAcquireEmitsAToken(t *testing.T) {
	p := New(1000, 0)
	defer p.Close()
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	if err := p.Acquire(ctx, "9.9.9.9"); err != nil {
		t.Errorf("Acquire = %v, want a token within 1s", err)
	}
}

func TestCloseStopsRefill(t *testing.T) {
	p := New(1000, 0)
	p.Close() // must not panic; goroutine exits
}
