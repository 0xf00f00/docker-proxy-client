// Package config is the manager's runtime configuration: mostly fixed behavior (Defaults),
// plus the few settings an operator owns (Load).
package config

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"
	"unicode"
)

// Instance is one managed sni-spoofing container: its name (also the container
// and the directory holding its config.ini), config path and selector state file.
type Instance struct{ Name, Conf, State string }

// Config is the manager's runtime configuration.
type Config struct {
	APIAddr string // control API bind address

	RangesFile string
	OutDir     string // pool.txt and friends
	CfstBin    string

	// cfst discovery.
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

	// Per-job caps (backstops; the queue is the primary control).
	ScanTimeout time.Duration
	TestTimeout time.Duration

	// Preflight skips a scan (keeping the previous pool) when no upstream is even
	// reachable. Lenient: any one target answering lets the scan proceed.
	Preflight bool

	ScanEnable bool   // scheduled discovery scans (the dashboard can always scan on demand)
	ScanCron   string // 6-field cron with leading seconds; empty = daily

	// Runtime edge selection. SelectEnable gates ALL autonomous behavior: probing,
	// quarantine, config rewrites and container restarts.
	SelectEnable       bool
	SelectInterval     time.Duration // normal cadence between ticks
	SelectMaxBackoff   time.Duration // cap during a sustained outage
	SelectSleepCap     time.Duration // max single sleep (keeps shutdown responsive)
	KeepMax            float64       // keep an in-use edge if first-attempt tcp_loss <= this
	PickMax            float64       // a replacement edge must beat this
	MaxCandidates      int           // pool IPs examined per pick
	MaxProbeCandidates int           // of those, how many get the real-path probe
	MinRestartGap      time.Duration
	QuarantineTTL      time.Duration
	LossPings          int           // tcp_loss connects per edge
	LossTimeout        time.Duration // per connect; must stay under the 1s SYN retransmit

	// Real-path probe (embedded sni-spoofing + xray). The wire params MUST match the
	// live xray "proxy" outbound.
	SurvivalCheck                           bool
	ProbeOriginHost, ProbeUUID, ProbePath   string
	ProbeRealUTLS, ProbeFakeUTLS            string
	ProbeFragment                           bool
	ProbeURL, XrayBin                       string
	ProbeCount, ProbeConcurrency            int
	ProbeKeepFailMax, ProbePickFailMax      float64 // largest share of failed probes: to keep an edge / to adopt one
	ProbeMinGap, ProbeMaxGap, ProbeCacheTTL time.Duration
	ProbePreGate                            bool // one DoH request first, to fail a dead edge fast
	ProbeDoHURL                             string

	// Restraint: on a lossy or blacked-out network no edge reading means anything.
	ControlEdges int           // pool edges sampled as controls, besides the instances' own
	GateSettle   int           // consecutive good readings before a hold is released
	HoldInterval time.Duration // recheck cadence while held
	ConfirmTicks int           // consecutive bad checks before an edge is replaced
	MaxRotations int           // edge changes per instance per 24h
	PoolMinShare float64       // a scan yielding less than this share of PoolSize keeps the old pool

	// Passive signal: the share of an instance's connections that hit an ACK timeout.
	AckWindow     time.Duration
	AckMax        float64 // credibly above this, the edge is bad
	AckMinSamples int     // attempts needed before the signal is trusted

	// The sni-spoofing instances the manager rewrites + restarts, in priority order
	// (always on different edges and decoys), and the byedpi front host + hosts file,
	// owned by the last instance.
	Instances                           []Instance
	FrontHost, CorednsHosts, DockerSock string

	SNIVersion string // embedded sni-spoofing module version, surfaced in /status
}

const (
	defaultInstances = "sni-spoofing,sni-spoofing-fallback"
	instanceDir      = "/instances"
	stateDir         = "/state"
)

// Defaults is the fixed behavior; deliberately not configurable.
func Defaults() Config {
	return Config{
		APIAddr: ":8088", RangesFile: "/cf-ranges.txt", OutDir: "/out", CfstBin: "cfst",
		Threads: 100, PingCount: 10, Port: 443, LossMax: 0.10, LatMax: 1000, PoolSize: 40,
		TestPings: 30, TestConcurrency: 3, TestCooldown: 30 * time.Second, TestQueueMax: 64,
		ScanTimeout: 6 * time.Hour, TestTimeout: 3 * time.Minute, Preflight: true,

		SelectInterval: 10 * time.Minute, SelectMaxBackoff: 6 * time.Hour, SelectSleepCap: 5 * time.Minute,
		KeepMax: 0.10, PickMax: 0.05, MaxCandidates: 3, MaxProbeCandidates: 2,
		MinRestartGap: 5 * time.Minute, QuarantineTTL: time.Hour,
		LossPings: 30, LossTimeout: 800 * time.Millisecond,

		SurvivalCheck: true, ProbePath: "/", ProbeRealUTLS: "chrome", ProbeFakeUTLS: "firefox",
		ProbeURL: "http://www.gstatic.com/generate_204", XrayBin: "/usr/local/bin/xray",
		ProbeCount: 8, ProbeConcurrency: 2, ProbeKeepFailMax: 0.5, ProbePickFailMax: 0,
		ProbeMinGap: 10 * time.Second, ProbeMaxGap: 5 * time.Minute, ProbeCacheTTL: 30 * time.Minute,
		ProbePreGate: true, ProbeDoHURL: "https://one.one.one.one/dns-query?name=cloudflare.com&type=A",

		ControlEdges: 6, GateSettle: 3, HoldInterval: 5 * time.Minute,
		ConfirmTicks: 3, MaxRotations: 4, PoolMinShare: 0.25,
		AckWindow: 15 * time.Minute, AckMax: 0.08, AckMinSamples: 30,

		CorednsHosts: "/coredns-fallback/edge.hosts", DockerSock: "/var/run/docker.sock",
		SNIVersion: "0.7.2",
	}
}

// Load is Defaults plus the operator's settings from the environment.
func Load() (Config, error) {
	c := Defaults()
	var err error
	if c.Instances, err = parseInstances(env("SNISPOOF_INSTANCES", defaultInstances), instanceDir, stateDir); err != nil {
		return Config{}, err
	}
	c.ScanEnable = envBool("SCAN_ENABLE", false)
	c.ScanCron = env("SCAN_CRON", "")
	c.SelectEnable = envBool("SELECT_ENABLE", false)
	c.ProbeUUID = env("PROBE_UUID", "")
	c.ProbePath = env("PROBE_PATH", c.ProbePath)
	c.ProbeOriginHost = env("PROBE_ORIGIN_HOST", "")
	c.FrontHost = env("FALLBACK_FRONT_HOST", "")
	return c, c.validate()
}

// validate fails at startup: without the wire identity every edge would read as failed.
func (c Config) validate() error {
	if c.SelectEnable && c.SurvivalCheck {
		switch {
		case c.ProbeUUID == "":
			return fmt.Errorf("SELECT_ENABLE=true requires PROBE_UUID (vless id of the main xray outbound)")
		case c.ProbeOriginHost == "":
			return fmt.Errorf("SELECT_ENABLE=true requires PROBE_ORIGIN_HOST (the fronted origin host)")
		}
	}
	return nil
}

var instanceName = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]*$`)

// parseInstances turns a comma/space separated list of container names into
// instances. A name is interpolated into a docker API path and file paths, hence
// the strict pattern.
func parseInstances(list, dir, stateDir string) ([]Instance, error) {
	var out []Instance
	for _, name := range strings.FieldsFunc(list, func(r rune) bool { return r == ',' || unicode.IsSpace(r) }) {
		switch {
		case !instanceName.MatchString(name):
			return nil, fmt.Errorf("SNISPOOF_INSTANCES: invalid name %q", name)
		case slices.ContainsFunc(out, func(i Instance) bool { return i.Name == name }):
			return nil, fmt.Errorf("SNISPOOF_INSTANCES: %q listed twice", name)
		}
		out = append(out, Instance{
			Name:  name,
			Conf:  filepath.Join(dir, name, "config.ini"),
			State: filepath.Join(stateDir, "picker-"+name+".json"),
		})
	}
	if len(out) == 0 {
		return nil, fmt.Errorf("SNISPOOF_INSTANCES must name at least one instance")
	}
	return out, nil
}

// PrimaryConf is the first instance's config, the decoy source for probes that
// aren't tied to one instance.
func (c Config) PrimaryConf() string {
	if len(c.Instances) == 0 {
		return ""
	}
	return c.Instances[0].Conf
}

func env(key, def string) string {
	if v, ok := os.LookupEnv(key); ok && v != "" {
		return v
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
