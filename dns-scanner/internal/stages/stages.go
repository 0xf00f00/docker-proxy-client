// Package stages implements the cheap-to-expensive funnel gates that run before
// the engine MTU-echo certification. Each gate operates on one reused Session.
package stages

import (
	"time"

	"mdns-scanner/internal/dnsprobe"
)

// Alive sends one A query for a random subdomain (1 retry). Any valid reply —
// regardless of rcode — proves the resolver is answering. Returns RTT.
func Alive(s *dnsprobe.Session, base string) (time.Duration, bool) {
	for i := 0; i < 2; i++ {
		if i > 0 {
			time.Sleep(150 * time.Millisecond)
		}
		pkt, id, err := dnsprobe.BuildQuery(dnsprobe.RandLabel(8)+"."+base, dnsprobe.TypeA, 0)
		if err != nil {
			return 0, false
		}
		start := time.Now()
		if _, err := s.Query(pkt, id); err == nil {
			return time.Since(start), true
		}
	}
	return 0, false
}

// NXDomain requires 2-of-3 random *.invalid names to return NXDOMAIN — filtering
// resolvers that hijack/inject answers for nonexistent names.
func NXDomain(s *dnsprobe.Session) bool {
	passes := 0
	for attempt := 1; attempt <= 3; attempt++ {
		pkt, id, err := dnsprobe.BuildQuery("nxd-"+dnsprobe.RandLabel(10)+".invalid", dnsprobe.TypeA, 0)
		if err != nil {
			return false
		}
		if resp, err := s.Query(pkt, id); err == nil && resp.Rcode == dnsprobe.RcodeNXDomain {
			passes++
			if passes >= 2 {
				return true
			}
		}
		if passes+(3-attempt) < 2 {
			return false
		}
	}
	return false
}

// Forwarding proves the resolver walks delegation to the tunnel domain (NS, then
// TXT fallback). NOERROR or NXDOMAIN both indicate a real recursive lookup.
func Forwarding(s *dnsprobe.Session, domain string) bool {
	return queryAccept(s, domain, dnsprobe.TypeNS) || queryAccept(s, domain, dnsprobe.TypeTXT)
}

func queryAccept(s *dnsprobe.Session, name string, qtype uint16) bool {
	pkt, id, err := dnsprobe.BuildQuery(name, qtype, 0)
	if err != nil {
		return false
	}
	resp, err := s.Query(pkt, id)
	if err != nil {
		return false
	}
	return resp.Rcode == dnsprobe.RcodeNoError || resp.Rcode == dnsprobe.RcodeNXDomain
}

// EDNS returns the largest advertised UDP payload size the resolver echoes back
// (1232 → 900 → 512), a coarse download-capacity signal. 0 = no usable EDNS.
func EDNS(s *dnsprobe.Session, domain string) int {
	for _, size := range []uint16{1232, 900, 512} {
		pkt, id, err := dnsprobe.BuildQuery(dnsprobe.RandLabel(6)+"."+domain, dnsprobe.TypeTXT, size)
		if err != nil {
			continue
		}
		resp, err := s.Query(pkt, id)
		if err != nil || resp.Rcode == dnsprobe.RcodeFormErr {
			continue
		}
		if resp.EDNSPayload >= int(size) {
			return int(size)
		}
	}
	return 0
}

// UploadCarriage sends a long base-encoded TXT QNAME (near the upload-MTU wire
// shape) and confirms it round-trips — a coarse upload-capacity signal.
func UploadCarriage(s *dnsprobe.Session, domain string) (time.Duration, bool) {
	// Budget the encoded prefix so total name length stays well under 253.
	budget := 230 - len(domain)
	if budget > 180 {
		budget = 180
	}
	if budget < 40 {
		budget = 40
	}
	qname := dnsprobe.RandLabels(budget) + "." + domain
	pkt, id, err := dnsprobe.BuildQuery(qname, dnsprobe.TypeTXT, 1232)
	if err != nil {
		return 0, false
	}
	start := time.Now()
	if _, err := s.Query(pkt, id); err != nil {
		return 0, false
	}
	return time.Since(start), true
}

// Loss sends n quick A queries and returns the loss fraction and RTT jitter
// (max−min of successes). Ranked, not gated — mdns tolerates loss via duplication.
func Loss(s *dnsprobe.Session, base string, n int) (lossFrac float64, jitter time.Duration) {
	ok := 0
	var min, max time.Duration
	for i := 0; i < n; i++ {
		pkt, id, err := dnsprobe.BuildQuery(dnsprobe.RandLabel(8)+"."+base, dnsprobe.TypeA, 0)
		if err != nil {
			continue
		}
		start := time.Now()
		if _, err := s.Query(pkt, id); err == nil {
			rtt := time.Since(start)
			ok++
			if min == 0 || rtt < min {
				min = rtt
			}
			if rtt > max {
				max = rtt
			}
		}
	}
	if n == 0 {
		return 0, 0
	}
	return float64(n-ok) / float64(n), max - min
}
