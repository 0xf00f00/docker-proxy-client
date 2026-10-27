// Package manager wires the tiered probes (tcp_loss + the embedded real-path
// prober), the config appliers, and the pool reader into a selector and runs its
// tick loop. It replaces the standalone cf-edge-picker + cf-edge-prober.
package manager

import (
	"context"
	"fmt"
	"log/slog"
	"math"
	"path/filepath"
	"time"

	"github.com/0xf00f00/cf-edge-manager/internal/apply"
	"github.com/0xf00f00/cf-edge-manager/internal/config"
	"github.com/0xf00f00/cf-edge-manager/internal/decoy"
	"github.com/0xf00f00/cf-edge-manager/internal/pool"
	"github.com/0xf00f00/cf-edge-manager/internal/probe"
	"github.com/0xf00f00/cf-edge-manager/internal/realpath"
	"github.com/0xf00f00/cf-edge-manager/internal/selector"
)

// instance is one managed sni-spoofing container and its selector.
type instance struct {
	name    string
	applier *apply.Applier
	sel     *selector.Selector
}

// Manager runs the runtime edge-selection loop and owns the embedded prober. The
// instances are kept on different edges and decoys; the last one also owns the
// byedpi path.
type Manager struct {
	cfg    config.Config
	log    *slog.Logger
	prober *realpath.Prober
	insts  []*instance // priority order
}

// New wires the probes, appliers, pool reader and one selector per instance.
func New(cfg config.Config, log *slog.Logger) *Manager {
	prober := realpath.New(ProberConfig(cfg), log)
	loss := func(ip string) float64 { return probe.TCPLoss(ip, cfg.Port, cfg.LossPings, cfg.LossTimeout) }
	poolFn := func() []string { return pool.Read(filepath.Join(cfg.OutDir, "pool.txt")) }

	m := &Manager{cfg: cfg, log: log, prober: prober}
	last := len(cfg.Instances) - 1
	for i, c := range cfg.Instances {
		a := &apply.Applier{Port: cfg.Port, SnispoofConf: c.Conf, SnispoofName: c.Name, DockerSock: cfg.DockerSock}
		if i == last {
			a.FrontHost, a.CorednsHosts = cfg.FrontHost, cfg.CorednsHosts
		}
		survival := func(ip string) *bool {
			return prober.Probe(context.Background(), ip, realpath.Options{FakeSNI: a.CurrentFakeSNI()}).Survived
		}
		sel := selector.New(selector.Config{
			KeepMax: cfg.KeepMax, PickMax: cfg.PickMax,
			MaxCandidates: cfg.MaxCandidates, MaxProbeCandidates: cfg.MaxProbeCandidates,
			BaseInterval: secs(cfg.SelectInterval), MaxBackoff: secs(cfg.SelectMaxBackoff),
			MinRestartGap: secs(cfg.MinRestartGap), QuarantineTTL: secs(cfg.QuarantineTTL),
			SurvivalCheck: cfg.SurvivalCheck, NoByedpi: i != last,
		}, log, c.State, loss, survival, poolFn, a)
		sel.SetDecoy(m.decoyHello, m.decoyTrial, decoy.Seeds())
		sel.SetPeers(m.peersOf(i))
		m.insts = append(m.insts, &instance{name: c.Name, applier: a, sel: sel})
	}
	return m
}

// peersOf is what instance i must keep clear of: the other instances' edges and
// decoys, plus the byedpi edge when another instance owns that path.
func (m *Manager) peersOf(i int) selector.Peers {
	return func() (edges, decoys []string) {
		for j, o := range m.insts {
			if j != i {
				edges = append(edges, o.applier.CurrentSnispoof())
				decoys = append(decoys, o.applier.CurrentFakeSNI())
			}
		}
		if owner := m.insts[len(m.insts)-1]; owner != m.insts[i] {
			edges = append(edges, owner.applier.CurrentByedpi())
		}
		return edges, decoys
	}
}

// ProberConfig builds the probe config; the wire params MUST match the live xray
// "proxy" outbound or the probe false-fails. Exported so an on-demand test can
// reuse the selector's exact parameters.
func ProberConfig(cfg config.Config) realpath.Config {
	return realpath.Config{
		OriginHost: cfg.ProbeOriginHost, UUID: cfg.ProbeUUID, Path: cfg.ProbePath,
		RealUTLS: cfg.ProbeRealUTLS, FakeSNI: apply.LiveFakeSNI(cfg.PrimaryConf()), FakeUTLS: cfg.ProbeFakeUTLS,
		Fragment: cfg.ProbeFragment, EdgePort: cfg.Port, ProbeURL: cfg.ProbeURL,
		PreGate: cfg.ProbePreGate, DoHURL: cfg.ProbeDoHURL,
		Count: cfg.ProbeCount, Concurrency: cfg.ProbeConcurrency, Spacing: 300 * time.Millisecond,
		MinGap: cfg.ProbeMinGap, MaxGap: cfg.ProbeMaxGap, CacheTTL: cfg.ProbeCacheTTL,
		ReadyWait: 15 * time.Second, XrayBin: cfg.XrayBin, SocksPort: 10808, SniPort: 40443,
	}
}

// ProbeSurvival runs an on-demand real-path probe on the manager's long-lived
// prober (reusing its xray/NFQUEUE), single-flighted against the selector.
func (m *Manager) ProbeSurvival(ctx context.Context, ip string) realpath.Result {
	return m.prober.Probe(ctx, ip, realpath.Options{Force: true, Interactive: true, FakeSNI: m.insts[0].applier.CurrentFakeSNI()})
}

// Start launches the long-lived xray subprocess and the select loop. An instance
// without a readable connect= would never be managed, so that is an error.
func (m *Manager) Start(ctx context.Context) error {
	for _, in := range m.insts {
		if in.applier.CurrentSnispoof() == "" {
			return fmt.Errorf("instance %s: no connect= in %s", in.name, in.applier.SnispoofConf)
		}
	}
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
		delay := time.Duration(m.tick()) * time.Second
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

// tick runs the due selectors in priority order, so the first instance gets the
// pick of the pool, and returns the seconds until the earliest next one.
func (m *Manager) tick() int64 {
	next := int64(math.MaxInt64)
	for _, in := range m.insts {
		next = min(next, in.sel.Tick(time.Now().Unix()))
	}
	return next
}

// Stop terminates the embedded xray subprocess.
func (m *Manager) Stop() { m.prober.Stop() }

// Status reports the in-use edge and decoy per instance, in priority order, plus
// the probe's sni-spoofing version (surfaced so drift against the live container
// is visible).
func (m *Manager) Status() map[string]any {
	insts := make([]map[string]string, 0, len(m.insts))
	for _, in := range m.insts {
		insts = append(insts, map[string]string{
			"name": in.name, "edge": in.applier.CurrentSnispoof(), "fake_sni": in.applier.CurrentFakeSNI(),
		})
	}
	return map[string]any{
		"instances":                  insts,
		"byedpi":                     m.insts[len(m.insts)-1].applier.CurrentByedpi(),
		"sni_spoofing_probe_version": m.cfg.SNIVersion,
		"survival_check":             m.cfg.SurvivalCheck,
	}
}

func secs(d time.Duration) int64 { return int64(d.Seconds()) }
