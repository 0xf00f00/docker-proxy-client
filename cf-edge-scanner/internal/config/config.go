// Package config loads the scanner's runtime configuration from the
// environment. Variable names mirror the previous bash implementation so the
// docker-compose contract is unchanged.
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

	// TLS-survival gate (post-rank, sequential, ~1 KB/candidate). Off by default.
	TLSCheck   bool
	TLSSNI     string
	TLSHold    time.Duration
	TLSTimeout time.Duration
	TLSGap     time.Duration
}

// Load reads and validates the configuration from environment variables,
// applying the documented defaults for any that are unset.
func Load() (Config, error) {
	c := Config{
		APIAddr:         env("CF_API_ADDR", ":8088"),
		RangesFile:      env("RANGES", "/cf-ranges.txt"),
		OutDir:          env("OUT_DIR", "/out"),
		CfstBin:         env("CFST_BIN", "cfst"),
		Threads:         envInt("THREADS", 5),
		PingCount:       envInt("PING_COUNT", 10),
		Port:            envInt("PORT", 443),
		LossMax:         envFloat("LOSS_MAX", 0.10),
		LatMax:          envInt("LAT_MAX", 1000),
		PoolSize:        envInt("POOL_SIZE", 10),
		TestPings:       envInt("TEST_PINGS", 30),
		TestConcurrency: envInt("CF_TEST_CONCURRENCY", 3),
		TestCooldown:    envSeconds("TEST_COOLDOWN", 30),
		TestQueueMax:    envInt("CF_TEST_QUEUE_MAX", 64),
		ScanTimeout:     envSeconds("SCAN_TIMEOUT", 21600),
		TestTimeout:     envSeconds("TEST_TIMEOUT", 180),
		Preflight:       envBool("CF_PREFLIGHT", true),
		TLSCheck:        envBool("TLS_CHECK", false),
		TLSSNI:          env("TLS_SNI", "hcaptcha.com"),
		TLSHold:         envSeconds("TLS_HOLD", 3),
		TLSTimeout:      envSeconds("TLS_TIMEOUT", 10),
		TLSGap:          envSeconds("TLS_GAP", 2),
	}
	if err := c.validate(); err != nil {
		return Config{}, err
	}
	return c, nil
}

func (c Config) validate() error {
	switch {
	case c.Threads < 1:
		return fmt.Errorf("THREADS must be >= 1, got %d", c.Threads)
	case c.PoolSize < 1:
		return fmt.Errorf("POOL_SIZE must be >= 1, got %d", c.PoolSize)
	case c.TestConcurrency < 1:
		return fmt.Errorf("CF_TEST_CONCURRENCY must be >= 1, got %d", c.TestConcurrency)
	case c.Port < 1 || c.Port > 65535:
		return fmt.Errorf("PORT must be 1-65535, got %d", c.Port)
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
