package manager

import (
	"context"
	"time"

	"github.com/0xf00f00/cf-edge-manager/internal/probe"
	"github.com/0xf00f00/cf-edge-manager/internal/realpath"
)

const (
	decoyHelloTimeout = 6 * time.Second
	decoyTrialTimeout = 3 * time.Minute
)

func (m *Manager) decoyHello(ctx context.Context, ip, sni string) bool {
	ctx, cancel := context.WithTimeout(ctx, decoyHelloTimeout)
	defer cancel()
	return probe.TLSHello(ctx, ip, m.cfg.Port, sni, m.cfg.ProbeFakeUTLS)
}

// decoyTrial probes ip with sni as the decoy, outside the selector's cadence/cache.
func (m *Manager) decoyTrial(ip, sni string) *bool {
	ctx, cancel := context.WithTimeout(context.Background(), decoyTrialTimeout)
	defer cancel()
	return m.prober.Probe(ctx, ip, realpath.Options{Force: true, Interactive: true, FakeSNI: sni}).Survived
}

// ScreenDecoys screens the decoy candidates by plain TLS now, without switching.
func (m *Manager) ScreenDecoys(ctx context.Context) map[string]any {
	return m.insts[0].sel.ScreenDecoys(ctx)
}
