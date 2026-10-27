package manager

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/0xf00f00/cf-edge-manager/internal/config"
	"github.com/0xf00f00/cf-edge-manager/internal/netgate"
)

// newTestManager builds one instance per name, on edges 192.0.2.1, .2, ... and
// decoys <name>.example; byedpi (owned by the last) sits on 198.51.100.9.
func newTestManager(t *testing.T, names ...string) *Manager {
	t.Helper()
	cfg := config.Defaults()
	cfg.OutDir, cfg.CorednsHosts, cfg.FrontHost = t.TempDir(), filepath.Join(t.TempDir(), "edge.hosts"), "front.example"
	cfg.GateSettle = 2
	if err := os.WriteFile(cfg.CorednsHosts, []byte("198.51.100.9 front.example\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for i, name := range names {
		conf := filepath.Join(t.TempDir(), "config.ini")
		ini := "connect = 192.0.2." + string(rune('1'+i)) + ":443\nfake-sni = " + name + ".example\n"
		if err := os.WriteFile(conf, []byte(ini), 0o600); err != nil {
			t.Fatal(err)
		}
		cfg.Instances = append(cfg.Instances, config.Instance{Name: name, Conf: conf, State: filepath.Join(t.TempDir(), name+".json")})
	}
	return New(cfg, slog.New(slog.NewTextHandler(io.Discard, nil)))
}

func writePool(t *testing.T, m *Manager, ips ...string) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(m.cfg.OutDir, "pool.txt"), []byte(strings.Join(ips, "\n")+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
}

func TestPeersAreEveryoneElse(t *testing.T) {
	m := newTestManager(t, "a", "b", "c")
	edges, decoys := m.peersOf(0)()
	if !slices.Equal(edges, []string{"192.0.2.2", "192.0.2.3", "198.51.100.9"}) || !slices.Equal(decoys, []string{"b.example", "c.example"}) {
		t.Errorf("first instance must avoid the others and the byedpi edge: %v %v", edges, decoys)
	}
	edges, decoys = m.peersOf(2)()
	if !slices.Equal(edges, []string{"192.0.2.1", "192.0.2.2"}) || !slices.Equal(decoys, []string{"a.example", "b.example"}) {
		t.Errorf("the byedpi owner already knows its own edge: %v %v", edges, decoys)
	}
}

// logSource serves, per container, [attempts, ACK timeouts] log lines.
type logSource map[string][2]int

func (l logSource) StartedAt(context.Context, string) (time.Time, error) { return time.Time{}, nil }

func (l logSource) Logs(_ context.Context, name string, _ time.Time) ([]string, error) {
	c, ok := l[name]
	if !ok {
		return nil, errors.New("no such container")
	}
	var lines []string
	for range c[0] {
		lines = append(lines, "injector: fake ClientHello sent")
	}
	for range c[1] {
		lines = append(lines, "proxy: ACK timeout after 2s")
	}
	return lines, nil
}

func TestAckBlamesOnlyTheInstanceThatIsFailing(t *testing.T) {
	m := newTestManager(t, "a", "b")
	m.logs = logSource{"a": {200, 50}, "b": {200, 2}}
	if a := m.ack("a"); !a.Bad || a.Systemic {
		t.Errorf("a failing 25%% beside a clean b is its own edge's fault: %+v", a)
	}
	if b := m.ack("b"); b.Bad {
		t.Errorf("b at 1%% must be fine: %+v", b)
	}
}

func TestAckIsSystemicWhenEveryTrustedInstanceFails(t *testing.T) {
	m := newTestManager(t, "a", "b", "c")
	m.logs = logSource{"a": {200, 50}, "b": {100, 40}, "c": {5, 5}} // c has too little traffic to vote
	for _, n := range []string{"a", "b"} {
		if a := m.ack(n); !a.Bad || !a.Systemic {
			t.Errorf("%s: %+v, want bad and systemic", n, a)
		}
	}
}

func TestAckUnreadableLogGivesNoSignal(t *testing.T) {
	m := newTestManager(t, "a")
	m.logs = logSource{}
	if a := m.ack("a"); a.Bad || a.Samples != 0 {
		t.Errorf("an unreadable log must never condemn an edge: %+v", a)
	}
}

// Everything unreachable is the network, not an edge: hold, and only trust it
// again once it has stayed good for GateSettle readings.
func TestBlackoutHoldsAndRecoveryNeedsToSettle(t *testing.T) {
	m := newTestManager(t, "a", "b")
	writePool(t, m, "198.51.100.10", "198.51.100.11", "198.51.100.12", "198.51.100.13")
	loss := 1.0
	m.controlLoss = func(string) float64 { return loss }
	read := func() string { m.gateMemo = memo[netgate.State]{}; return m.Hold() }

	if got := read(); got != "blackout" {
		t.Fatalf("Hold = %q, want blackout", got)
	}
	loss = 0
	if got := read(); got != "blackout" {
		t.Errorf("Hold = %q after one good reading, want still held", got)
	}
	if got := read(); got != "" {
		t.Errorf("Hold = %q after two good readings, want released", got)
	}
}

// One throttled edge among healthy controls is exactly the case to act on.
func TestOneBadEdgeAmongHealthyControlsIsNotAnOutage(t *testing.T) {
	m := newTestManager(t, "a", "b")
	writePool(t, m, "198.51.100.10", "198.51.100.11", "198.51.100.12", "198.51.100.13")
	m.controlLoss = func(ip string) float64 {
		if ip == "192.0.2.1" {
			return 0.6
		}
		return 0
	}
	if got := m.Hold(); got != "" {
		t.Errorf("Hold = %q; a single bad edge must still be judged on its own", got)
	}
}

type sinceSource struct {
	logSource
	since *time.Time
}

func (s sinceSource) Logs(ctx context.Context, name string, since time.Time) ([]string, error) {
	*s.since = since
	return s.logSource.Logs(ctx, name, since)
}

// Failures from an outage must not condemn the edge once the network is back.
func TestPassiveWindowStartsAtTheRecoveryNotTheOutage(t *testing.T) {
	m := newTestManager(t, "a")
	writePool(t, m, "198.51.100.10", "198.51.100.11", "198.51.100.12")
	loss := 1.0
	m.controlLoss = func(string) float64 { return loss }
	for range 3 {
		m.gateMemo = memo[netgate.State]{}
		m.Hold()
		loss = 0
	}
	var since time.Time
	m.logs = sinceSource{logSource{"a": {100, 50}}, &since}
	m.ack("a")
	if recovered := m.gate.State().NormalSince; since.Before(recovered) {
		t.Errorf("window began %v, before the network recovered at %v", since, recovered)
	}
}
