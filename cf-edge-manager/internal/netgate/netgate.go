// Package netgate separates "this edge is bad" from "the network is bad": during a
// blackout or a lossy stretch every edge reads bad and acting on it only churns. Control
// edges unrelated to the instance are measured at the same moment, and the edge is only
// to blame when they are fine.
package netgate

import (
	"slices"
	"sync"
	"time"
)

// Regime is the state of the network as seen from the controls.
type Regime int

// Regimes, trustworthy first.
const (
	Normal   Regime = iota
	Degraded        // widespread loss: an edge can't be told apart from the network
	Blackout        // (almost) nothing is reachable
)

func (r Regime) String() string {
	switch r {
	case Degraded:
		return "degraded"
	case Blackout:
		return "blackout"
	}
	return "normal"
}

// A control is down at this first-attempt loss. Fewer than blackoutUp of the controls
// up is a blackout; fewer than degradedUp, or a median loss above degradedMedian, is
// degraded. With fewer than minControls the network can't be told from an edge.
const (
	downLoss       = 0.5
	blackoutUp     = 0.2
	degradedUp     = 0.7
	degradedMedian = 0.15
	minControls    = 3
)

// Classify grades the network from the first-attempt connect loss of each control.
func Classify(losses []float64) Regime {
	if len(losses) < minControls {
		return Normal
	}
	up := 0
	for _, l := range losses {
		if l < downLoss {
			up++
		}
	}
	share := float64(up) / float64(len(losses))
	switch {
	case share < blackoutUp:
		return Blackout
	case share < degradedUp || median(losses) > degradedMedian:
		return Degraded
	}
	return Normal
}

func median(v []float64) float64 {
	s := slices.Sorted(slices.Values(v))
	n := len(s)
	if n%2 == 1 {
		return s[n/2]
	}
	return (s[n/2-1] + s[n/2]) / 2
}

// State is a snapshot of the gate.
type State struct {
	Regime      Regime
	Since       time.Time // when the regime last changed
	NormalSince time.Time // when the network last became trustworthy; evidence from before it is stale
}

// Hold is why nothing should change right now ("" when the network is trusted).
func (s State) Hold() string {
	if s.Regime == Normal {
		return ""
	}
	return s.Regime.String()
}

// Gate turns readings into a regime with hysteresis: it holds at the first bad reading
// and releases only after settle consecutive normal ones, so a flapping network never
// reads as recovered between flaps.
type Gate struct {
	mu     sync.Mutex
	settle int
	good   int
	st     State
}

// New returns a gate that trusts the network as of now.
func New(settle int, now time.Time) *Gate {
	return &Gate{settle: max(1, settle), st: State{Since: now, NormalSince: now}}
}

// Observe records a reading and returns the regime before and after it.
func (g *Gate) Observe(now time.Time, r Regime) (from, to Regime) {
	g.mu.Lock()
	defer g.mu.Unlock()
	from = g.st.Regime
	switch {
	case r != Normal:
		g.good = 0
		if r != g.st.Regime {
			g.st.Regime, g.st.Since = r, now
		}
	case g.st.Regime != Normal:
		if g.good++; g.good >= g.settle {
			g.st = State{Regime: Normal, Since: now, NormalSince: now}
			g.good = 0
		}
	}
	return from, g.st.Regime
}

// State returns the current snapshot.
func (g *Gate) State() State {
	g.mu.Lock()
	defer g.mu.Unlock()
	return g.st
}
