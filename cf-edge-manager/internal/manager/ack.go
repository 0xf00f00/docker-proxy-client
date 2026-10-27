package manager

import (
	"context"
	"time"

	"github.com/0xf00f00/cf-edge-manager/internal/handshake"
	"github.com/0xf00f00/cf-edge-manager/internal/selector"
)

const ackTTL = 30 * time.Second

// ack is the passive verdict for the named instance; all instances are read together
// because "every instance is failing" needs them side by side.
func (m *Manager) ack(name string) selector.Ack {
	return m.ackMemo.get(ackTTL, m.readAcks)[name]
}

func (m *Manager) readAcks() map[string]selector.Ack {
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	// failures from before the network last recovered say nothing about the edge
	since := m.gate.State().NormalSince
	stats := map[string]handshake.Stat{}
	for _, in := range m.insts {
		st, err := handshake.Window(ctx, m.logs, in.name, time.Now(), m.cfg.AckWindow, since)
		if err != nil {
			m.log.Warn("handshake log unavailable; no passive signal", "instance", in.name, "err", err)
			continue
		}
		stats[in.name] = st
	}
	bad := func(st handshake.Stat) bool { return st.Bad(m.cfg.AckMax, m.cfg.AckMinSamples) }
	var trusted, failing int
	for _, st := range stats {
		if st.Total >= m.cfg.AckMinSamples {
			trusted++
			if bad(st) {
				failing++
			}
		}
	}
	systemic := trusted >= 2 && failing == trusted
	out := make(map[string]selector.Ack, len(stats))
	for name, st := range stats {
		out[name] = selector.Ack{Ratio: st.Ratio(), Samples: st.Total, Bad: bad(st), Systemic: bad(st) && systemic}
	}
	return out
}
