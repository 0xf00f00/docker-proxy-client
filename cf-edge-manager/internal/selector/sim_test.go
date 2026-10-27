package selector

import (
	"math/rand/v2"
	"testing"
	"time"

	"github.com/0xf00f00/cf-edge-manager/internal/config"
	"github.com/0xf00f00/cf-edge-manager/internal/netgate"
)

const (
	day    = int64(86400)
	step   = int64(300) // the manager loop wakes at least this often
	bucket = int64(600)
)

// hostileMonth: the in-use edge blips (one bad check) in 3% of 10-minute buckets; days
// 2-9 are a total blackout; day 9-10 flaps; a flaky 2h follows each outage; on day 20
// the in-use edge degrades for good.
type hostileMonth struct {
	spikes [4400]bool
	flaky  *rand.Rand
}

func (h *hostileMonth) down(t int64) bool {
	switch {
	case t >= 2*day && t < 9*day:
		return true
	case t >= 9*day && t < 10*day:
		return (t-9*day)%3600 >= 2400
	}
	return false
}

func (h *hostileMonth) flakyAround(t int64) bool {
	for _, end := range []int64{9 * day, 10 * day} {
		if t >= end && t < end+7200 {
			return true
		}
	}
	return false
}

type simResult struct {
	rotations, restarts, probes, rotationsInOutage int
	detectLatency                                  int64
}

func runMonth(t *testing.T, seed uint64, cfg Config) simResult {
	t.Helper()
	h := &hostileMonth{flaky: rand.New(rand.NewPCG(seed, 1))}
	rng := rand.New(rand.NewPCG(seed, 2))
	for i := range h.spikes {
		h.spikes[i] = rng.Float64() < 0.03
	}
	var now int64
	var bad string
	res := simResult{detectLatency: -1}
	ap := &fakeApplier{snispoof: "cur", liveConnect: "cur"}
	pool := []string{"cur", "c1", "c2", "c3", "c4", "c5", "c6", "c7"}

	probeUp := func() bool { return !h.down(now) && !(h.flakyAround(now) && h.flaky.Float64() < 0.5) }
	lossOf := func(ip string) float64 {
		res.probes++
		switch {
		case !probeUp():
			return 1
		case ip == bad:
			return 0.5
		case ip == ap.snispoof && h.spikes[now/bucket%int64(len(h.spikes))]:
			return 0.5
		}
		return 0
	}
	s := newSel(t, cfg, nil, nil, pool, ap)
	s.loss = lossOf
	s.survival = func(string, bool) *bool { return bptr(probeUp()) }
	gate := netgate.New(config.Defaults().GateSettle, time.Unix(0, 0))
	s.Use(Hooks{Hold: func() string { return gate.State().Hold() }})

	var prevWrites int
	for ; now < 30*day; now += step {
		controls := make([]float64, 8)
		for i := range controls {
			if !probeUp() {
				controls[i] = 1
			}
		}
		gate.Observe(time.Unix(now, 0), netgate.Classify(controls))
		if now == 20*day {
			bad = ap.snispoof
		}
		s.Tick(now)
		if n := len(ap.wroteS); n > prevWrites {
			res.rotations += n - prevWrites
			if h.down(now) || h.flakyAround(now) {
				res.rotationsInOutage += n - prevWrites
			}
			if bad != "" && res.detectLatency < 0 && ap.snispoof != bad {
				res.detectLatency = now - 20*day
			}
			prevWrites = n
		}
	}
	res.restarts = ap.restarts
	return res
}

// The restraint that matters on this network: no edge changes during outages,
// blips don't move the edge, and a genuinely bad edge is still replaced.
func TestHostileMonthDoesNotChurn(t *testing.T) {
	d := config.Defaults()
	cfg := testCfg()
	cfg.ConfirmTicks, cfg.MaxRotations, cfg.HoldInterval = d.ConfirmTicks, d.MaxRotations, int64(d.HoldInterval.Seconds())
	for seed := uint64(1); seed <= 5; seed++ {
		r := runMonth(t, seed, cfg)
		if r.rotationsInOutage != 0 {
			t.Errorf("seed %d: %d edge changes during an outage", seed, r.rotationsInOutage)
		}
		if r.rotations > 3 {
			t.Errorf("seed %d: %d edge changes in a month (one is the real failure)", seed, r.rotations)
		}
		if r.detectLatency < 0 || r.detectLatency > 2400 {
			t.Errorf("seed %d: a really degraded edge was replaced after %ds, want within 40 minutes", seed, r.detectLatency)
		}
	}
}
