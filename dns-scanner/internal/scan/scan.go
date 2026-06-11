// Package scan runs the funnel (cheap gates + engine certification) over a set
// of IPs with a bounded worker pool, optionally stopping once N are accepted.
package scan

import (
	"context"
	"sync"
	"sync/atomic"
	"time"

	"mdns-scanner/internal/certify"
	"mdns-scanner/internal/dnsprobe"
	"mdns-scanner/internal/pacer"
	"mdns-scanner/internal/score"
	"mdns-scanner/internal/stages"
)

// certifyTimeout bounds a single engine MTU-echo certification round-trip.
const certifyTimeout = 30 * time.Second

// Deps are the shared dependencies for a scan. Prober may be nil (gates-only
// dry run); then "accepted" means the cheap gates passed.
type Deps struct {
	Prober       *certify.Prober
	Pacer        *pacer.Pacer
	BindIP       string
	ProbeDomain  string
	StageTimeout time.Duration
	LossSamples  int
	Workers      int
	CertifyConc  int
	OnAccept     func(r score.Result, n int)
	OnProbe      func()
	Gate         func(context.Context) error

	// CertifyAnyway certifies Dial+Alive survivors even when the cheap gates
	// flake — set for the verify leg so a gate flake can't veto a known-good
	// resolver. The sweep leaves it false (gates economize unknowns).
	CertifyAnyway bool
	// OnCertResult fires once per certification attempt with its verdict.
	OnCertResult func(ip string, accepted bool, reason string)
}

// Run funnels each ip through the gates (and certification when a prober is set)
// via a bounded worker pool. When stopAtTarget, it cancels once targetN are
// accepted. Returns the accepted Results and the number of IPs probed.
func Run(ctx context.Context, d Deps, ips []string, targetN int, stopAtTarget bool) ([]score.Result, int) {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()

	ipCh := make(chan string, 256)
	certSem := make(chan struct{}, max(1, d.CertifyConc))
	var (
		results  []score.Result
		mu       sync.Mutex
		accepted int64
		probed   int64
		wg       sync.WaitGroup
	)

	workers := max(1, d.Workers)
	for i := 0; i < workers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for ip := range ipCh {
				if d.Gate != nil {
					if err := d.Gate(ctx); err != nil {
						continue
					}
				}
				if ctx.Err() != nil {
					continue
				}
				if err := d.Pacer.Acquire(ctx, ip); err != nil {
					continue
				}
				atomic.AddInt64(&probed, 1)
				if d.OnProbe != nil {
					d.OnProbe()
				}
				res := runResolver(ctx, d, certSem, ip)
				if res.GatesPassed() && (d.Prober == nil || res.Certified) {
					mu.Lock()
					results = append(results, res)
					mu.Unlock()
					n := atomic.AddInt64(&accepted, 1)
					if d.OnAccept != nil {
						d.OnAccept(res, int(n))
					}
					if stopAtTarget && n >= int64(targetN) {
						cancel()
					}
				}
			}
		}()
	}

feed:
	for _, ip := range ips {
		select {
		case <-ctx.Done():
			break feed
		case ipCh <- ip:
		}
	}
	close(ipCh)
	wg.Wait()
	return results, int(atomic.LoadInt64(&probed))
}

func runResolver(ctx context.Context, d Deps, certSem chan struct{}, ip string) score.Result {
	r := score.Result{IP: ip}

	s, err := dnsprobe.Dial(ip, 53, d.BindIP, d.StageTimeout)
	if err != nil {
		return r
	}
	defer s.Close()

	rtt, ok := stages.Alive(s, "example.com")
	if !ok {
		return r
	}
	r.AliveRTT = rtt

	// Cheap gates. CertifyAnyway runs them for ranking but won't short-circuit a
	// known-good resolver out of certification; the sweep short-circuits dead ones.
	r.NXOK = stages.NXDomain(s)
	if !r.NXOK && !d.CertifyAnyway {
		return r
	}
	r.Forwards = stages.Forwarding(s, d.ProbeDomain)
	if !r.Forwards && !d.CertifyAnyway {
		return r
	}
	r.EDNSMax = stages.EDNS(s, d.ProbeDomain)
	if _, up := stages.UploadCarriage(s, d.ProbeDomain); up {
		r.UploadOK = true
	}
	r.LossFrac, r.Jitter = stages.Loss(s, "example.com", d.LossSamples)

	if d.Prober == nil || !(r.GatesPassed() || d.CertifyAnyway) {
		return r
	}

	select {
	case certSem <- struct{}{}:
	case <-ctx.Done():
		return r
	}
	defer func() { <-certSem }()

	cctx, ccancel := context.WithTimeout(ctx, certifyTimeout)
	out := d.Prober.Probe(cctx, ip, 53, d.StageTimeout)
	ccancel()
	r.Certified = out.Accepted
	r.UploadMTU = out.UploadBytes
	r.DownloadMTU = out.DownloadBytes
	r.CertRTTms = out.RTTMillis
	r.CertifyReason = out.Reason
	if d.OnCertResult != nil {
		d.OnCertResult(ip, out.Accepted, out.Reason)
	}
	return r
}

// Anchors reports whether any of ips still recursively resolves domain
// (Dial → Alive → Forwarding). It is the outage-recovery canary's probe:
// trigger-only — it promotes nothing and writes nothing. domain must be
// neutral (never the secret tunnel zone).
func Anchors(ctx context.Context, d Deps, ips []string, domain string) bool {
	for _, ip := range ips {
		if ctx.Err() != nil {
			return false
		}
		s, err := dnsprobe.Dial(ip, 53, d.BindIP, d.StageTimeout)
		if err != nil {
			continue
		}
		_, alive := stages.Alive(s, "example.com")
		forwards := alive && stages.Forwarding(s, domain)
		s.Close()
		if forwards {
			return true
		}
	}
	return false
}
