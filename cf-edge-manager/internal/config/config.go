// Package config loads the scanner's runtime configuration from the
// environment. Variable names are grouped by subsystem prefix, carry explicit
// unit suffixes (_S/_MS) and metric-bearing names (_LOSS_MAX), and use _ENABLE
// for toggles; docker-compose.yml maps the user-facing CF_<NAME> vars onto them.
package config

import (
	"fmt"
	"os"
	"strconv"
	"time"
)

// Config is the scanner's runtime configuration, sourced from the environment.
type Config struct {
	// HTTP control API bind address (served on the bridge network, published to
	// 127.0.0.1 on the host; the host cannot reach the macvlan egress IP).
	APIAddr string

	// Filesystem layout (bind-mounted /out, read-only ranges file).
	RangesFile string
	OutDir     string
	CfstBin    string

	// cfst probing knobs (passed straight through to the binary).
	Threads   int
	PingCount int
	Port      int
	LossMax   float64
	LatMax    int
	PoolSize  int

	// Single-IP interactive test.
	TestPings       int
	TestConcurrency int
	TestCooldown    time.Duration // serve a cached result instead of re-probing
	TestQueueMax    int           // reject (429) once this many tests are pending

	// Hard per-job caps (backstops; the queue is the primary control).
	ScanTimeout time.Duration
	TestTimeout time.Duration

	// Preflight skips a scan (keeping the previous pool) when no upstream target
	// is even TCP-reachable -- avoids burning a full scan window during a total
	// outage. Lenient by design: any one target answering lets the scan proceed.
	Preflight bool

	// ScanCron schedules the periodic full discovery scan (6-field cron, seconds
	// leading; evaluated in the container TZ). Empty is the DEFAULT and disables
	// scheduled scans entirely -- automated work is opt-in, so out of the box
	// nothing runs on a timer (the dashboard can still trigger a scan on demand).
	// Set e.g. "0 0 5 * * 1" (Mondays 05:00) to enable.
	ScanCron string

	// Fake-SNI connect-survival gate (post-rank; one candidate at a time, but a
	// concurrent burst within each, ~TLSBurst KB/candidate). Off by default.
	// Drops candidates whose burst failure rate exceeds TLSFailMax and re-ranks
	// the survivors cleanest-first, so the picker inherits a survival-ordered
	// pool rather than a TCP-loss-ordered one (a raw ping can't see the
	// concurrency-triggered per-edge resets that break real use).
	TLSCheck   bool
	TLSSNI     string
	TLSUTLS    string // gate-handshake uTLS fingerprint; the gate sends only the decoy hello, so match PROBE_FAKE_UTLS
	TLSHold    time.Duration
	TLSTimeout time.Duration
	TLSGap     time.Duration
	TLSBurst   int     // concurrent fake-SNI sessions per candidate
	TLSFailMax float64 // drop a candidate whose burst failure rate exceeds this

	// ExpectEgressPrefix, when set (e.g. "10.0.0.240/28"), makes the scanner warn
	// if its probes egress on an address outside this CIDR — a guard that the
	// container dials from the macvlan eth0 IP and not the VPN default route
	// (which would measure the wrong path). Empty disables the check.
	ExpectEgressPrefix string

	// Manager (runtime edge selection — the old picker + prober, now in-process).
	// SelectEnable is the master switch for ALL autonomous runtime behavior: the
	// select loop, real-path probing, quarantine, config rewrites, and restarting
	// sni-spoofing-fallback. It defaults OFF so a fresh `up` does nothing on its
	// own; set SELECT_ENABLE=true to opt into automatic edge rotation.
	SelectEnable       bool
	SelectInterval     time.Duration // normal cadence between ticks
	SelectMaxBackoff   time.Duration // cap during a sustained outage
	SelectSleepCap     time.Duration // max single sleep (keeps shutdown responsive)
	KeepMax            float64       // keep an in-use edge if tcp_loss <= this
	PickMax            float64       // a replacement edge must beat this (tcp_loss)
	MaxCandidates      int           // pool IPs examined per pick
	MaxProbeCandidates int           // of those, how many get the expensive real-path probe
	MinRestartGap      time.Duration
	QuarantineTTL      time.Duration
	LossPings          int           // tcp_loss connects per edge
	LossTimeout        time.Duration // per-connect timeout

	// Real-path survival probe (embedded sni-spoofing + xray). SurvivalCheck gates
	// the expensive tier; the wire params MUST match the live xray "proxy" outbound.
	SurvivalCheck                              bool
	ProbeOriginHost, ProbeUUID, ProbePath      string
	ProbeRealUTLS, ProbeFakeSNI, ProbeFakeUTLS string // RealUTLS: xray real-hello fp; FakeUTLS: sni-spoofing decoy-hello fp
	ProbeFragment                              bool
	ProbeURL, XrayBin                          string
	ProbeCount, ProbeConcurrency               int
	ProbeMinGap, ProbeMaxGap, ProbeCacheTTL    time.Duration

	// ProbePreGate runs one DoH-JSON request through the tunnel before the full
	// survival burst: a tunnelled DoH query is opaque to DPI (just xHTTP bytes to
	// a CF edge), so it succeeds iff the whole method works end-to-end — a cheap
	// "is this edge usable at all" signal that short-circuits the expensive burst
	// on a dead edge. (Distinct from the direct-DoH block: that resets plaintext
	// DoH for resolution; this is encapsulated.) ProbeDoHURL is the HTTPS endpoint.
	ProbePreGate bool
	ProbeDoHURL  string

	// Apply targets (the fallback edge-path configs the manager rewrites).
	FrontHost, CorednsHosts, SnispoofConf string
	SnispoofName, DockerSock, SelectState string

	// SNIVersion is the embedded sni-spoofing module version, surfaced in /status
	// so drift against the live container is visible.
	SNIVersion string
}

// Load reads and validates the configuration from environment variables,
// applying the documented defaults for any that are unset.
func Load() (Config, error) {
	// Env var names are grouped by prefix (SCAN_/TEST_/TLS_/SELECT_/PROBE_), carry
	// an explicit unit suffix where they hold a duration or rate (_S seconds,
	// _MS milliseconds, _MAX a 0..1 fraction), and use _ENABLE for on/off toggles.
	// docker-compose.yml maps the user-facing CF_<NAME> vars onto these. See the
	// migration table in README.md for the old (pre-rename) names.
	c := Config{
		APIAddr:         env("API_ADDR", ":8088"),
		RangesFile:      env("RANGES_FILE", "/cf-ranges.txt"),
		OutDir:          env("OUT_DIR", "/out"),
		CfstBin:         env("CFST_BIN", "cfst"),
		Threads:         envInt("SCAN_THREADS", 5),
		PingCount:       envInt("SCAN_CONNECTS", 10),
		Port:            envInt("EDGE_PORT", 443),
		LossMax:         envFloat("SCAN_LOSS_MAX", 0.10),
		LatMax:          envInt("SCAN_LAT_MAX_MS", 1000),
		PoolSize:        envInt("POOL_SIZE", 10),
		TestPings:       envInt("TEST_CONNECTS", 30),
		TestConcurrency: envInt("TEST_CONCURRENCY", 3),
		TestCooldown:    envSeconds("TEST_COOLDOWN_S", 30),
		TestQueueMax:    envInt("TEST_QUEUE_MAX", 64),
		ScanTimeout:     envSeconds("SCAN_TIMEOUT_S", 21600),
		TestTimeout:     envSeconds("TEST_TIMEOUT_S", 180),
		Preflight:       envBool("SCAN_PREFLIGHT", true),
		ScanCron:        env("SCAN_CRON", ""), // empty = no scheduled scans (opt-in)
		TLSCheck:        envBool("TLS_GATE_ENABLE", false),
		TLSSNI:          env("TLS_SNI", "hcaptcha.com"),
		TLSUTLS:         env("TLS_UTLS", "firefox"),
		TLSHold:         envSeconds("TLS_HOLD_S", 3),
		TLSTimeout:      envSeconds("TLS_TIMEOUT_S", 10),
		TLSGap:          envSeconds("TLS_GAP_S", 2),
		TLSBurst:        envInt("TLS_BURST", 12),
		TLSFailMax:      envFloat("TLS_FAIL_MAX", 0.10),

		ExpectEgressPrefix: env("SCAN_EGRESS_PREFIX", ""),

		SelectEnable:       envBool("SELECT_ENABLE", false),
		SelectInterval:     envSeconds("SELECT_INTERVAL_S", 600),
		SelectMaxBackoff:   envSeconds("SELECT_MAX_BACKOFF_S", 21600),
		SelectSleepCap:     envSeconds("SELECT_SLEEP_CAP_S", 300),
		KeepMax:            envFloat("KEEP_LOSS_MAX", 0.20),
		PickMax:            envFloat("PICK_LOSS_MAX", 0.10),
		MaxCandidates:      envInt("SELECT_MAX_CANDIDATES", 3),
		MaxProbeCandidates: envInt("PROBE_MAX_CANDIDATES", 2),
		MinRestartGap:      envSeconds("RESTART_MIN_GAP_S", 300),
		QuarantineTTL:      envSeconds("QUARANTINE_TTL_S", 3600),
		LossPings:          envInt("LOSS_CONNECTS", 10),
		LossTimeout:        envSeconds("LOSS_TIMEOUT_S", 2),

		SurvivalCheck:    envBool("PROBE_ENABLE", true),
		ProbeOriginHost:  env("PROBE_ORIGIN_HOST", ""),
		ProbeUUID:        env("PROBE_UUID", ""),
		ProbePath:        env("PROBE_PATH", "/"),
		ProbeRealUTLS:    env("PROBE_REAL_UTLS", "chrome"),
		ProbeFakeSNI:     env("PROBE_FAKE_SNI", "hcaptcha.com"),
		ProbeFakeUTLS:    env("PROBE_FAKE_UTLS", "firefox"),
		ProbeFragment:    envBool("PROBE_FRAGMENT", false),
		ProbeURL:         env("PROBE_URL", "http://www.gstatic.com/generate_204"),
		XrayBin:          env("XRAY_BIN", "/usr/local/bin/xray"),
		ProbeCount:       envInt("PROBE_COUNT", 4),
		ProbeConcurrency: envInt("PROBE_CONCURRENCY", 2),
		ProbeMinGap:      envSeconds("PROBE_MIN_GAP_S", 10),
		ProbeMaxGap:      envSeconds("PROBE_MAX_GAP_S", 300),
		ProbeCacheTTL:    envSeconds("PROBE_CACHE_TTL_S", 1800),
		ProbePreGate:     envBool("PROBE_PREGATE_ENABLE", true),
		ProbeDoHURL:      env("PROBE_DOH_URL", "https://one.one.one.one/dns-query?name=cloudflare.com&type=A"),

		FrontHost:    env("FALLBACK_FRONT_HOST", ""),
		CorednsHosts: env("COREDNS_HOSTS", "/coredns-fallback/edge.hosts"),
		SnispoofConf: env("SNISPOOF_CONF", "/snispoof2/config.ini"),
		SnispoofName: env("SNISPOOF_CONTAINER", "sni-spoofing-fallback"),
		DockerSock:   env("DOCKER_SOCK", "/var/run/docker.sock"),
		SelectState:  env("SELECT_STATE", "/state/picker.json"),

		SNIVersion: "0.7.2",
	}
	if err := c.validate(); err != nil {
		return Config{}, err
	}
	return c, nil
}

func (c Config) validate() error {
	switch {
	case c.Threads < 1:
		return fmt.Errorf("SCAN_THREADS must be >= 1, got %d", c.Threads)
	case c.PoolSize < 1:
		return fmt.Errorf("POOL_SIZE must be >= 1, got %d", c.PoolSize)
	case c.TestConcurrency < 1:
		return fmt.Errorf("TEST_CONCURRENCY must be >= 1, got %d", c.TestConcurrency)
	case c.Port < 1 || c.Port > 65535:
		return fmt.Errorf("EDGE_PORT must be 1-65535, got %d", c.Port)
	}
	// The probe wire params are only consumed once the autonomous loop is on, so
	// a bare `up` (automation off) doesn't need them. But if the loop IS enabled
	// with the real-path probe, missing identity params make every edge read as a
	// false failure -- fail loudly at startup instead of silently mis-rotating.
	if c.SelectEnable && c.SurvivalCheck {
		switch {
		case c.ProbeUUID == "":
			return fmt.Errorf("SELECT_ENABLE=true with PROBE_ENABLE requires PROBE_UUID (vless id of the main xray outbound)")
		case c.ProbeOriginHost == "":
			return fmt.Errorf("SELECT_ENABLE=true with PROBE_ENABLE requires PROBE_ORIGIN_HOST (the fronted origin host)")
		}
	}
	return nil
}

func env(key, def string) string {
	if v, ok := os.LookupEnv(key); ok && v != "" {
		return v
	}
	return def
}

func envInt(key string, def int) int {
	if v, ok := os.LookupEnv(key); ok {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return def
}

func envFloat(key string, def float64) float64 {
	if v, ok := os.LookupEnv(key); ok {
		if f, err := strconv.ParseFloat(v, 64); err == nil {
			return f
		}
	}
	return def
}

func envBool(key string, def bool) bool {
	if v, ok := os.LookupEnv(key); ok {
		if b, err := strconv.ParseBool(v); err == nil {
			return b
		}
	}
	return def
}

// envSeconds reads an integer count of seconds (the unit the bash config used).
func envSeconds(key string, def int) time.Duration {
	return time.Duration(envInt(key, def)) * time.Second
}
