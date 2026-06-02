// Package manager wires the tiered probes (tcp_loss + the embedded real-path
// prober), the config appliers, and the pool reader into a selector and runs its
// tick loop. It replaces the standalone cf-edge-picker + cf-edge-prober.
package manager

import (
	"context"
	"log/slog"
	"path/filepath"
	"time"

	"github.com/0xf00f00/cf-edge-manager/internal/apply"
	"github.com/0xf00f00/cf-edge-manager/internal/config"
	"github.com/0xf00f00/cf-edge-manager/internal/pool"
	"github.com/0xf00f00/cf-edge-manager/internal/probe"
	"github.com/0xf00f00/cf-edge-manager/internal/realpath"
	"github.com/0xf00f00/cf-edge-manager/internal/selector"
)

// Manager runs the runtime edge-selection loop and owns the embedded prober.
type Manager struct {
	cfg     config.Config
	log     *slog.Logger
	prober  *realpath.Prober
	applier *apply.Applier
	sel     *selector.Selector
}

// New wires the probes, applier, pool reader and selector from config.
func New(cfg config.Config, log *slog.Logger) *Manager {
	applier := &apply.Applier{
		FrontHost: cfg.FrontHost, Port: cfg.Port,
		CorednsHosts: cfg.CorednsHosts, SnispoofConf: cfg.SnispoofConf,
		SnispoofName: cfg.SnispoofName, DockerSock: cfg.DockerSock,
	}
	prober := realpath.New(realpath.Config{
		OriginHost: cfg.ProbeOriginHost, UUID: cfg.ProbeUUID, Path: cfg.ProbePath,
		RealUTLS: cfg.ProbeRealUTLS, FakeSNI: cfg.ProbeFakeSNI, FakeUTLS: cfg.ProbeFakeUTLS,
		Fragment: cfg.ProbeFragment, EdgePort: cfg.Port, ProbeURL: cfg.ProbeURL,
		PreGate: cfg.ProbePreGate, DoHURL: cfg.ProbeDoHURL,
		Count: cfg.ProbeCount, Concurrency: cfg.ProbeConcurrency, Spacing: 300 * time.Millisecond,
		MinGap: cfg.ProbeMinGap, MaxGap: cfg.ProbeMaxGap, CacheTTL: cfg.ProbeCacheTTL,
		ReadyWait: 15 * time.Second, XrayBin: cfg.XrayBin, SocksPort: 10808, SniPort: 40443,
	}, log)

	loss := func(ip string) float64 { return probe.TCPLoss(ip, cfg.Port, cfg.LossPings, cfg.LossTimeout) }
	survival := func(ip string) *bool { return prober.Probe(ip, false).Survived }
	poolFn := func() []string { return pool.Read(filepath.Join(cfg.OutDir, "pool.txt")) }

	sel := selector.New(selector.Config{
		KeepMax: cfg.KeepMax, PickMax: cfg.PickMax,
		MaxCandidates: cfg.MaxCandidates, MaxProbeCandidates: cfg.MaxProbeCandidates,
		BaseInterval: secs(cfg.SelectInterval), MaxBackoff: secs(cfg.SelectMaxBackoff),
		MinRestartGap: secs(cfg.MinRestartGap), QuarantineTTL: secs(cfg.QuarantineTTL),
		SurvivalCheck: cfg.SurvivalCheck,
	}, log, cfg.SelectState, loss, survival, poolFn, applier)

	return &Manager{cfg: cfg, log: log, prober: prober, applier: applier, sel: sel}
}

// Start launches the long-lived xray subprocess and the select loop.
func (m *Manager) Start(ctx context.Context) error {
	if err := m.prober.Start(); err != nil {
		return err
	}
	go m.loop(ctx)
	m.log.Info("manager started", "interval_s", secs(m.cfg.SelectInterval), "survival_check", m.cfg.SurvivalCheck)
	return nil
}

func (m *Manager) loop(ctx context.Context) {
	sleepCap := m.cfg.SelectSleepCap
	for {
		delay := time.Duration(m.sel.Tick(time.Now().Unix())) * time.Second
		if delay > sleepCap {
			delay = sleepCap // re-tick at the cap; Tick re-gates on next_run (keeps shutdown responsive)
		}
		if delay < time.Second {
			delay = time.Second
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(delay):
		}
	}
}

// Stop terminates the embedded xray subprocess.
func (m *Manager) Stop() { m.prober.Stop() }

// Status reports the current in-use edges + the probe's sni-spoofing version
// (surfaced so drift against the live 0.5.1 container is visible).
func (m *Manager) Status() map[string]any {
	return map[string]any{
		"byedpi":                     m.applier.CurrentByedpi(),
		"snispoof":                   m.applier.CurrentSnispoof(),
		"sni_spoofing_probe_version": m.cfg.SNIVersion,
		"survival_check":             m.cfg.SurvivalCheck,
	}
}

func secs(d time.Duration) int64 { return int64(d.Seconds()) }
