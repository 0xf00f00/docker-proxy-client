package manager

import (
	"math/rand/v2"
	"path/filepath"
	"slices"
	"sync"
	"time"

	"github.com/0xf00f00/cf-edge-manager/internal/netgate"
	"github.com/0xf00f00/cf-edge-manager/internal/pool"
	"github.com/0xf00f00/cf-edge-manager/internal/probe"
)

const (
	gateTTL         = time.Minute // readings are a few dozen SYNs; no more often than this
	holdReminder    = 6 * time.Hour
	controlConnects = 10
)

// Hold is why no edge should be changed right now ("" when the network is trusted).
func (m *Manager) Hold() string {
	return m.gateMemo.get(gateTTL, m.measureGate).Hold()
}

func (m *Manager) measureGate() netgate.State {
	ctrl := m.controls()
	losses := make([]float64, len(ctrl))
	var wg sync.WaitGroup
	for i, ip := range ctrl {
		wg.Add(1)
		go func() { defer wg.Done(); losses[i] = m.controlLoss(ip) }()
	}
	wg.Wait()

	now := time.Now()
	from, to := m.gate.Observe(now, netgate.Classify(losses))
	switch {
	case from != to:
		m.log.Warn("network regime changed", "from", from, "to", to, "control_loss", losses)
		m.gateNoted = now
	case to != netgate.Normal && now.Sub(m.gateNoted) >= holdReminder:
		m.log.Info("network still not trusted; holding", "state", to, "since", m.gate.State().Since.Format(time.RFC3339))
		m.gateNoted = now
	}
	return m.gate.State()
}

// controls are the instances' own edges, known to be in use, plus a random pool sample.
func (m *Manager) controls() []string {
	var out []string
	for _, in := range m.insts {
		if ip := in.applier.CurrentSnispoof(); ip != "" && !slices.Contains(out, ip) {
			out = append(out, ip)
		}
	}
	rest := slices.DeleteFunc(pool.Read(filepath.Join(m.cfg.OutDir, "pool.txt")), func(ip string) bool { return slices.Contains(out, ip) })
	rand.Shuffle(len(rest), func(i, j int) { rest[i], rest[j] = rest[j], rest[i] })
	return append(out, rest[:min(len(rest), m.cfg.ControlEdges)]...)
}

func defaultControlLoss(m *Manager) func(string) float64 {
	return func(ip string) float64 { return probe.TCPLoss(ip, m.cfg.Port, controlConnects, m.cfg.LossTimeout) }
}
