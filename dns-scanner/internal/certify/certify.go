package certify

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// Result is the certification verdict. Accepted is true only when the resolver
// carried both an upload and a download mdns round-trip to the live server.
type Result struct {
	Accepted      bool
	Reason        string
	UploadBytes   int
	DownloadBytes int
	RTTMillis     int64
}

// verdict mirrors the JSON the `probe` subcommand prints on stdout.
type verdict struct {
	Accepted      bool   `json:"accepted"`
	Reason        string `json:"reason"`
	UploadBytes   int    `json:"uploadBytes"`
	UploadChars   int    `json:"uploadChars"`
	DownloadBytes int    `json:"downloadBytes"`
	RTTMillis     int64  `json:"rttMillis"`
}

// Config configures a Prober. Bin is the mdns client binary. ConfigPath, if set,
// is an existing client config file the probe reads directly (key stays in that
// file). Otherwise certify generates one from Domain+Key (+method/MTU), which
// are required for certification.
type Config struct {
	Bin        string
	ConfigPath string
	Domain     string
	Key        string
	Method     int
	BaseEncode bool
	MinUp      int
	MaxUp      int
	MinDown    int
	MaxDown    int
}

// Prober certifies resolvers via the external probe binary. Safe for concurrent
// use — each Probe spawns its own short-lived process reading the same config.
type Prober struct {
	bin     string
	cfgPath string
}

// ErrNoBinary signals the probe binary is unconfigured or not found — the caller
// degrades to gates-only rather than failing.
var ErrNoBinary = fmt.Errorf("certify: no probe binary available")

// ErrNoProbeSubcommand signals the binary exists but lacks a usable `probe`
// subcommand — every certification would fail silently (the 0-of-N incident).
// Unlike ErrNoBinary this is fatal: the caller must NOT degrade to gates-only.
var ErrNoProbeSubcommand = fmt.Errorf("certify: probe binary lacks a usable `probe` subcommand")

// New builds a Prober.
func New(c Config) (*Prober, error) {
	if c.ConfigPath == "" && (c.Domain == "" || c.Key == "") {
		return nil, nil
	}
	if c.Bin == "" {
		return nil, ErrNoBinary
	}
	path, err := exec.LookPath(c.Bin)
	if err != nil {
		return nil, fmt.Errorf("%w: %q: %v", ErrNoBinary, c.Bin, err)
	}
	if err := verifyProbeSubcommand(path); err != nil {
		return nil, err
	}

	cfgPath := c.ConfigPath
	if cfgPath == "" {
		cfgPath, err = writeProbeConfig(c)
		if err != nil {
			return nil, fmt.Errorf("certify: write probe config: %w", err)
		}
	} else if _, err := os.Stat(cfgPath); err != nil {
		return nil, fmt.Errorf("certify: MDNS_CLIENT_CONFIG not readable: %w", err)
	}
	return &Prober{bin: path, cfgPath: cfgPath}, nil
}

// writeProbeConfig renders a minimal client JSON config (keyed by the engine's
// TOML field names, which the client's JSON loader understands) to a 0600 file.
// The key lives in this file, never on the probe's argv. Single fixed path so
// scanner restarts overwrite rather than accumulate secrets in temp.
func writeProbeConfig(c Config) (string, error) {
	m := map[string]any{
		"ENCRYPTION_KEY":         c.Key,
		"DOMAINS":                []string{c.Domain},
		"DATA_ENCRYPTION_METHOD": c.Method,
		"BASE_ENCODE_DATA":       c.BaseEncode,
		"LOG_LEVEL":              "ERROR",
	}
	if c.MinUp > 0 {
		m["MIN_UPLOAD_MTU"] = c.MinUp
	}
	if c.MaxUp > 0 {
		m["MAX_UPLOAD_MTU"] = c.MaxUp
	}
	if c.MinDown > 0 {
		m["MIN_DOWNLOAD_MTU"] = c.MinDown
	}
	if c.MaxDown > 0 {
		m["MAX_DOWNLOAD_MTU"] = c.MaxDown
	}
	b, err := json.Marshal(m)
	if err != nil {
		return "", err
	}
	path := filepath.Join(os.TempDir(), "mdns-scanner-probe.json")
	if err := os.WriteFile(path, b, 0o600); err != nil {
		return "", err
	}
	return path, nil
}

// Probe certifies one resolver at ip:port.
func (p *Prober) Probe(ctx context.Context, ip string, port int, timeout time.Duration) Result {
	args := []string{
		"probe",
		"-config", p.cfgPath,
		"-ip", ip,
		"-port", strconv.Itoa(port),
	}
	if timeout > 0 {
		args = append(args, "-timeout", timeout.String())
	}

	cmd := exec.CommandContext(ctx, p.bin, args...)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	if err := cmd.Run(); err != nil {
		if ctx.Err() != nil {
			return Result{Reason: "cancelled"}
		}
		return Result{Reason: "exec: " + firstLine(stderr.String())}
	}

	var v verdict
	if err := json.Unmarshal(bytes.TrimSpace(stdout.Bytes()), &v); err != nil {
		return Result{Reason: "parse"}
	}
	return Result{
		Accepted:      v.Accepted,
		Reason:        v.Reason,
		UploadBytes:   v.UploadBytes,
		DownloadBytes: v.DownloadBytes,
		RTTMillis:     v.RTTMillis,
	}
}

func firstLine(s string) string {
	if i := bytes.IndexByte([]byte(s), '\n'); i >= 0 {
		return s[:i]
	}
	return s
}

func verifyProbeSubcommand(bin string) error {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, bin, "probe", "-h")
	var out bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &out
	_ = cmd.Run() // -h exits non-zero via flag.ErrHelp; inspect output, not exit code
	s := out.String()
	if strings.Contains(s, "-ip") && strings.Contains(s, "-config") {
		return nil
	}
	return fmt.Errorf("%w (%q `probe -h` did not list its flags: %q)",
		ErrNoProbeSubcommand, bin, firstLine(strings.TrimSpace(s)))
}

// Returns the probe binary's reported version line
func ProbeVersion(bin string) string {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	out, _ := exec.CommandContext(ctx, bin, "-version").CombinedOutput()
	line := firstLine(strings.TrimSpace(string(out)))
	low := strings.ToLower(line)
	if line != "" && len(line) < 80 && strings.Contains(line, ".") &&
		strings.ContainsAny(line, "0123456789") && !strings.Contains(low, "usage") {
		return line
	}
	return ""
}
