package selector

import (
	"context"
	"slices"

	"github.com/0xf00f00/cf-edge-manager/internal/decoy"
)

const (
	decoyScreenEdges = 2    // edges for the plain-TLS screen
	decoyTrialEdges  = 3    // edges tried per real-path trial
	decoyMaxTrials   = 3    // candidates trialled per search
	decoyGap         = 1800 // s; no decoy check after a plain-TLS miss for this long
)

// SetDecoy enables decoy self-healing: hello is a plain TLS hello, trial a
// real-path probe with the given decoy, cands the candidates in preference order.
func (s *Selector) SetDecoy(hello decoy.Check, trial func(ip, sni string) *bool, cands []string) {
	s.hello, s.trial, s.decoys = hello, trial, cands
}

// ScreenDecoys screens the live decoy and the candidates by plain TLS, without switching.
func (s *Selector) ScreenDecoys(ctx context.Context) map[string]any {
	cur := s.apply.CurrentFakeSNI()
	edges := decoy.Edges(s.apply.CurrentSnispoof(), s.pool(), decoyScreenEdges)
	cands := s.decoys
	if decoy.Valid(cur) && !slices.Contains(cands, cur) {
		cands = append([]string{cur}, cands...)
	}
	scores := decoy.Screen(ctx, edges, cands, s.hello)
	return map[string]any{"current": cur, "edges": edges, "scores": scores, "ranked": decoy.Rank(scores)}
}

// trialAny reports whether sni survives the real path on any of edges.
func (s *Selector) trialAny(edges []string, sni string) bool {
	for _, ip := range edges {
		if r := s.trial(ip, sni); r != nil && *r {
			return true
		}
	}
	return false
}

// checkDecoy replaces a decoy DPI has started dropping; edge rotation can't help
// then, since every connection carries it. It only writes the config (Tick
// restarts) and reports whether it did. Cost when healthy: one plain TLS hello.
// When no replacement works it records the decoy in st.DecoyDead.
func (s *Selector) checkDecoy(st *State, now int64, pool []string, sip string) bool {
	if s.hello == nil || !s.cfg.SurvivalCheck || now < st.DecoyCheckAfter {
		return false
	}
	cur := s.apply.CurrentFakeSNI()
	edges := decoy.Edges(sip, pool, decoyTrialEdges)
	if cur == "" || len(edges) == 0 {
		return false
	}
	st.DecoyDead = ""
	screen := edges[:min(decoyScreenEdges, len(edges))]
	ctx := context.Background()
	if decoy.Screen(ctx, screen, []string{cur}, s.hello)[0].Pass > 0 {
		return false
	}
	st.DecoyCheckAfter = now + decoyGap
	// an edge that doesn't serve the name also fails plain TLS: confirm
	s.log.Info("decoy: plain TLS missed; confirming on real path", "sni", cur, "edges", screen)
	if s.trialAny(edges, cur) {
		s.log.Info("decoy: works on real path; keeping", "sni", cur)
		return false
	}
	_, peerDecoys := s.peerSets()
	var cands []string
	for _, c := range s.decoys {
		if c != cur && !slices.Contains(peerDecoys, c) {
			cands = append(cands, c)
		}
	}
	ranked := decoy.Rank(decoy.Screen(ctx, screen, cands, s.hello))
	if len(ranked) > 0 { // other names pass plain TLS where cur misses, and cur fails the real path
		st.DecoyDead = cur
	}
	for _, sni := range ranked[:min(decoyMaxTrials, len(ranked))] {
		if !s.trialAny(edges, sni) {
			s.log.Info("decoy: candidate failed real path", "sni", sni)
			continue
		}
		if err := s.apply.WriteFakeSNI(sni); err != nil {
			s.log.Error("decoy: write config", "sni", sni, "err", err)
			return false
		}
		st.DecoyDead, st.RestartPending = "", true
		s.log.Info("decoy: SWITCHED", "from", cur, "to", sni)
		return true
	}
	s.log.Warn("decoy: failing on real path; no candidate replaced it", "sni", cur, "passed_plain", len(ranked))
	return false
}
