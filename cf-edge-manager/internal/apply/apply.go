// Package apply performs the selector's side-effects: rewriting the byedpi
// (coredns hosts) and sni-spoofing (.ini connect=) edge configs, and restarting
// the sni-spoofing container. It is the "apply half" of the old picker — pure
// I/O, no policy. The selector owns WHEN to restart (rate-limit); this owns HOW.
//
// Docker restart talks to the engine over the unix socket directly (a one-line
// POST), avoiding the heavy docker SDK dependency.
package apply

import (
	"context"
	"fmt"
	"net"
	"net/http"
	"os"
	"regexp"
	"strings"
	"time"
)

var (
	connectRe    = regexp.MustCompile(`(?m)^(\s*connect\s*=\s*).*$`)
	connectValRe = regexp.MustCompile(`(?m)^\s*connect\s*=\s*([^:\s]+)`)
)

// Applier rewrites the two edge-path configs and restarts the snispoof container.
type Applier struct {
	FrontHost    string // byedpi front domain written into the coredns hosts file
	Port         int    // edge port (443)
	CorednsHosts string // path to coredns fallback hosts file
	SnispoofConf string // path to sni-spoofing .ini
	SnispoofName string // container name to restart
	DockerSock   string // /var/run/docker.sock
}

// CurrentByedpi returns the edge IP currently written in the coredns hosts file.
func (a *Applier) CurrentByedpi() string {
	b, err := os.ReadFile(a.CorednsHosts)
	if err != nil {
		return ""
	}
	for _, ln := range strings.Split(string(b), "\n") {
		s := strings.TrimSpace(ln)
		if s != "" && !strings.HasPrefix(s, "#") {
			return strings.Fields(s)[0]
		}
	}
	return ""
}

// CurrentSnispoof returns the edge IP currently in the sni-spoofing .ini connect=.
func (a *Applier) CurrentSnispoof() string {
	b, err := os.ReadFile(a.SnispoofConf)
	if err != nil {
		return ""
	}
	if m := connectValRe.FindStringSubmatch(string(b)); m != nil {
		return m[1]
	}
	return ""
}

// WriteByedpi rewrites the coredns hosts file to point the front domain at ip
// (no restart needed — coredns re-reads the hosts file).
func (a *Applier) WriteByedpi(ip string) error {
	if a.FrontHost == "" {
		return fmt.Errorf("FrontHost unset; cannot manage byedpi path")
	}
	return os.WriteFile(a.CorednsHosts, []byte(fmt.Sprintf("%s %s\n", ip, a.FrontHost)), 0o644) //nolint:gosec
}

// WriteSnispoof rewrites the .ini connect= line to ip:Port (no restart).
func (a *Applier) WriteSnispoof(ip string) error {
	b, err := os.ReadFile(a.SnispoofConf)
	if err != nil {
		return err
	}
	out := connectRe.ReplaceAll(b, []byte(fmt.Sprintf("${1}%s:%d", ip, a.Port)))
	return os.WriteFile(a.SnispoofConf, out, 0o644) //nolint:gosec
}

// RestartSnispoof restarts the sni-spoofing container via the docker engine API.
func (a *Applier) RestartSnispoof() error {
	tr := &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
		return (&net.Dialer{}).DialContext(ctx, "unix", a.DockerSock)
	}}
	cl := &http.Client{Transport: tr, Timeout: 30 * time.Second}
	url := fmt.Sprintf("http://docker/containers/%s/restart?t=10", a.SnispoofName)
	resp, err := cl.Post(url, "application/json", nil)
	if err != nil {
		return err
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusNoContent {
		return fmt.Errorf("docker restart %s: status %d", a.SnispoofName, resp.StatusCode)
	}
	return nil
}
