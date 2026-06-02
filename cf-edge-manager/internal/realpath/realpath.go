// Package realpath probes whether a Cloudflare edge survives DPI on the real
// production path: local socks -> xray (vless/xHTTP) -> in-process sni-spoofing
// (fake-SNI) -> edge -> fronted origin. A fake-SNI-only check was measured to
// disagree with production, so this is the only survival signal we trust. xray
// runs as one long-lived bundled binary; only sni-spoofing's connect varies per
// edge (cancel ctx + re-Run). Expensive: single-flight, backoff on inconclusive
// runs, per-edge cache.
package realpath

import (
	"bufio"
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/exec"
	"sni-spoofing-go/injection"
	"strconv"
	"strings"
	"sync"
	"time"

	snicfg "sni-spoofing-go/config"

	sniproxy "sni-spoofing-go/proxy"

	xproxy "golang.org/x/net/proxy"
)

// Config is the real-path probe configuration. The wire params MUST match the
// live xray "proxy" outbound or the probe would false-fail.
type Config struct {
	OriginHost string // fronted origin / xray serverName+host (required, no default)
	UUID       string // vless user id
	Path       string // xHTTP path
	RealUTLS   string // xray real-hello uTLS fingerprint (e.g. chrome)
	FakeSNI    string // sni-spoofing decoy SNI (e.g. hcaptcha.com)
	FakeUTLS   string // sni-spoofing decoy-hello uTLS fingerprint (e.g. firefox)
	Fragment   bool   // sni-spoofing real-ClientHello fragmentation (prod: false)
	EdgePort   int    // 443

	ProbeURL    string // a 0-byte endpoint reached THROUGH the tunnel (e.g. http://www.gstatic.com/generate_204)
	PreGate     bool   // run one DoH-JSON request through the tunnel before the burst
	DoHURL      string // HTTPS DoH endpoint for the pre-gate (e.g. https://one.one.one.one/dns-query?...)
	Count       int    // requests per probe (keep small)
	Concurrency int    // at most this many at once
	Spacing     time.Duration
	MinGap      time.Duration // base backoff between probes
	MaxGap      time.Duration // cap for the exponential backoff
	CacheTTL    time.Duration
	ReadyWait   time.Duration // max wait for the local stack to accept

	XrayBin   string // path to the bundled xray binary
	SocksPort int    // local xray socks inbound
	SniPort   int    // local sni-spoofing listener
}

// Result is one survival verdict. Survived==nil means INCONCLUSIVE (our own
// stack failed to come up) — callers must treat nil as "no signal", never as an
// edge failure.
type Result struct {
	IP       string  `json:"ip"`
	Survived *bool   `json:"survived"`
	FailRate float64 `json:"fail_rate"`
	Fails    int     `json:"fails"`
	Probes   int     `json:"probes"`
	Cached   bool    `json:"cached"`
	Err      string  `json:"error,omitempty"`
}

type cacheEntry struct {
	at  time.Time
	res Result
}

// Prober owns the long-lived xray subprocess and serialises probes.
type Prober struct {
	cfg Config
	log *slog.Logger

	mu      sync.Mutex // single-flight: fixed local ports => one probe at a time
	backoff float64    // multiplier on MinGap
	last    time.Time

	cmu   sync.Mutex
	cache map[string]cacheEntry

	xray *exec.Cmd
}

// New returns a Prober; call Start (launches xray) before Probe.
func New(cfg Config, log *slog.Logger) *Prober {
	return &Prober{cfg: cfg, log: log, backoff: 1, cache: map[string]cacheEntry{}}
}

// Start launches the long-lived xray subprocess (fixed config -> the in-process
// sni listener). It does NOT start sni-spoofing; that is (re)started per edge.
func (p *Prober) Start() error {
	cfgJSON, err := p.xrayConfig()
	if err != nil {
		return err
	}
	const path = "/tmp/manager_xray.json"
	if err := os.WriteFile(path, cfgJSON, 0o600); err != nil {
		return err
	}
	cmd := exec.Command(p.cfg.XrayBin, "run", "-c", path) //nolint:gosec // fixed bundled binary
	if err := cmd.Start(); err != nil {
		return fmt.Errorf("start xray: %w", err)
	}
	p.xray = cmd
	p.log.Info("realpath: xray started", "pid", cmd.Process.Pid, "socks", p.cfg.SocksPort)
	return nil
}

// Stop terminates the xray subprocess.
func (p *Prober) Stop() {
	if p.xray != nil && p.xray.Process != nil {
		_ = p.xray.Process.Kill()
		_, _ = p.xray.Process.Wait()
	}
}

// Probe returns the survival verdict for ip, serving a fresh cached result when
// available. force bypasses the cache.
func (p *Prober) Probe(ip string, force bool) Result {
	if !force {
		if r, ok := p.cached(ip); ok {
			return r
		}
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	if !force { // re-check under the lock (another caller may have just probed)
		if r, ok := p.cached(ip); ok {
			return r
		}
	}
	gap := min(time.Duration(float64(p.cfg.MinGap)*p.backoff), p.cfg.MaxGap)
	if wait := gap - time.Since(p.last); wait > 0 {
		time.Sleep(wait)
	}
	res := p.run(ip)
	p.last = time.Now()
	if res.Survived == nil { // struggling -> slow down
		p.backoff = min(p.backoff*2, float64(p.cfg.MaxGap)/float64(p.cfg.MinGap))
	} else { // conclusive -> base cadence, and cache it
		p.backoff = 1
		p.cmu.Lock()
		p.cache[ip] = cacheEntry{at: time.Now(), res: res}
		p.cmu.Unlock()
	}
	return res
}

func (p *Prober) cached(ip string) (Result, bool) {
	p.cmu.Lock()
	defer p.cmu.Unlock()
	if e, ok := p.cache[ip]; ok && time.Since(e.at) <= p.cfg.CacheTTL {
		r := e.res
		r.Cached = true
		return r, true
	}
	return Result{}, false
}

// run stands up sni-spoofing for ip, probes, tears it down.
func (p *Prober) run(ip string) Result {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel() // stops the embedded sni proxy (NFQUEUE teardown)

	ready := make(chan sniproxy.Ready, 1)
	runErr := make(chan error, 1)
	go func() { runErr <- sniproxy.Run(ctx, p.sniConfig(ip), p.sniOptions(), ready) }()

	select {
	case r := <-ready:
		if r.Err != nil {
			return Result{IP: ip, Err: "sni not ready: " + r.Err.Error()}
		}
	case e := <-runErr:
		return Result{IP: ip, Err: "sni exited: " + errStr(e)}
	case <-time.After(p.cfg.ReadyWait):
		return Result{IP: ip, Err: "sni ready timeout"}
	}
	// xray's socks must also be accepting (it is long-lived, but guard the first call)
	if !waitDial(net.JoinHostPort("127.0.0.1", strconv.Itoa(p.cfg.SocksPort)), time.Now().Add(p.cfg.ReadyWait)) {
		return Result{IP: ip, Err: "xray socks not ready"}
	}

	// Cheap pre-gate: one DoH-JSON query through the tunnel. It's opaque to DPI
	// (just xHTTP bytes to a CF edge), so success means the whole method works
	// end-to-end. A clean reset/timeout here means this edge isn't usable — fail
	// fast and skip the (Count-request) burst rather than spend it on a dead edge.
	if p.cfg.PreGate {
		if err := p.dohPreGate(); err != nil {
			fail := true
			p.log.Info("realpath: pre-gate failed; skipping burst", "ip", ip, "err", errStr(err))
			return Result{IP: ip, Survived: &fail, Fails: p.cfg.Count, Probes: p.cfg.Count, FailRate: 1, Err: "pregate: " + errStr(err)}
		}
	}

	fails := p.burst()
	survived := fails < p.cfg.Count
	return Result{
		IP: ip, Survived: &survived, Fails: fails, Probes: p.cfg.Count,
		FailRate: float64(fails) / float64(p.cfg.Count),
	}
}

// burst sends Count requests through the tunnel, at most Concurrency at a time,
// spaced. Returns the fail count.
func (p *Prober) burst() int {
	socks := net.JoinHostPort("127.0.0.1", strconv.Itoa(p.cfg.SocksPort))
	fails, done := 0, 0
	for done < p.cfg.Count {
		batch := p.cfg.Concurrency
		if rem := p.cfg.Count - done; rem < batch {
			batch = rem
		}
		results := make([]bool, batch)
		var wg sync.WaitGroup
		for i := range batch {
			wg.Add(1)
			go func() { defer wg.Done(); results[i] = p.oneRequest(socks) }()
		}
		wg.Wait()
		for _, ok := range results {
			if !ok {
				fails++
			}
		}
		done += batch
		if done < p.cfg.Count {
			time.Sleep(p.cfg.Spacing)
		}
	}
	return fails
}

// oneRequest dials a fresh connection through the socks proxy and fetches the
// 0-byte probe URL, requiring a 2xx/204. Any error/reset is a failure.
func (p *Prober) oneRequest(socksAddr string) bool {
	dialer, err := xproxy.SOCKS5("tcp", socksAddr, nil, &net.Dialer{Timeout: 15 * time.Second})
	if err != nil {
		return false
	}
	host, path := splitURL(p.cfg.ProbeURL)
	conn, err := dialer.Dial("tcp", host) // remote DNS via the proxy
	if err != nil {
		return false
	}
	defer func() { _ = conn.Close() }()
	_ = conn.SetDeadline(time.Now().Add(15 * time.Second))
	req := fmt.Sprintf("GET %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: curl\r\nConnection: close\r\n\r\n", path, hostOnly(host))
	if _, err := conn.Write([]byte(req)); err != nil {
		return false
	}
	line, err := bufio.NewReader(conn).ReadString('\n')
	if err != nil {
		return false
	}
	return strings.Contains(line, " 204") || strings.Contains(line, " 200")
}

// dohPreGate performs one DoH-JSON request through the SOCKS tunnel and requires
// a 200 with a well-formed JSON body. Unlike oneRequest (plaintext to a 0-byte
// endpoint), this does a real TLS handshake to the DoH host THROUGH the tunnel —
// the same shape as live traffic — and validates a parseable answer, so a CF
// challenge page or truncated reply can't false-pass it. The cert IS verified
// here (real hostname, system roots): we want to know real DoH works end-to-end.
func (p *Prober) dohPreGate() error {
	socks := net.JoinHostPort("127.0.0.1", strconv.Itoa(p.cfg.SocksPort))
	dialer, err := xproxy.SOCKS5("tcp", socks, nil, &net.Dialer{Timeout: 15 * time.Second})
	if err != nil {
		return err
	}
	host, path := dohTarget(p.cfg.DoHURL)
	raw, err := dialer.Dial("tcp", host) // remote DNS via the proxy
	if err != nil {
		return err
	}
	defer func() { _ = raw.Close() }()
	_ = raw.SetDeadline(time.Now().Add(15 * time.Second))

	conn := tls.Client(raw, &tls.Config{ServerName: hostOnly(host), MinVersion: tls.VersionTLS12})
	if err := conn.Handshake(); err != nil {
		return err
	}
	req := fmt.Sprintf("GET %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: curl\r\nAccept: application/dns-json\r\nConnection: close\r\n\r\n", path, hostOnly(host))
	if _, err := conn.Write([]byte(req)); err != nil {
		return err
	}
	resp, err := http.ReadResponse(bufio.NewReader(conn), nil)
	if err != nil {
		return err
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("doh status %s", resp.Status)
	}
	var payload any
	if err := json.NewDecoder(resp.Body).Decode(&payload); err != nil {
		return fmt.Errorf("doh body not JSON: %w", err)
	}
	return nil
}

func (p *Prober) sniConfig(ip string) *snicfg.Config {
	return &snicfg.Config{
		ListenHost: "127.0.0.1", ListenPort: p.cfg.SniPort,
		ConnectIP: ip, ConnectIPv4s: []string{ip}, ConnectPort: p.cfg.EdgePort,
		FakeSNI: p.cfg.FakeSNI, UTLSClientHello: p.cfg.FakeUTLS,
	}
}

func (p *Prober) sniOptions() sniproxy.Options {
	return sniproxy.Options{
		FakeRepeat: 1, FakeDelay: 2 * time.Millisecond,
		EnableFragment: p.cfg.Fragment, FragmentDelay: 500 * time.Millisecond,
		SNIChunk: 3, AckTimeout: 2 * time.Second, Quiet: true,
		Injector: injection.InjectorModeActive,
	}
}

func (p *Prober) xrayConfig() ([]byte, error) {
	cfg := map[string]any{
		"log": map[string]any{"loglevel": "warning"},
		"inbounds": []any{map[string]any{
			"tag": "in", "listen": "127.0.0.1", "port": p.cfg.SocksPort,
			"protocol": "socks", "settings": map[string]any{"udp": true, "auth": "noauth"},
		}},
		"outbounds": []any{map[string]any{
			"tag": "out", "protocol": "vless",
			"settings": map[string]any{"vnext": []any{map[string]any{
				"address": "127.0.0.1", "port": p.cfg.SniPort,
				"users": []any{map[string]any{"id": p.cfg.UUID, "level": 8, "encryption": "none", "flow": ""}},
			}}},
			"streamSettings": map[string]any{
				"network": "xhttp", "security": "tls",
				"tlsSettings":   map[string]any{"allowInsecure": false, "serverName": p.cfg.OriginHost, "alpn": []any{"h2"}, "fingerprint": p.cfg.RealUTLS},
				"xHttpSettings": map[string]any{"host": p.cfg.OriginHost, "mode": "auto", "path": p.cfg.Path},
			},
			"mux": map[string]any{"enabled": false},
		}},
	}
	return json.Marshal(cfg)
}
