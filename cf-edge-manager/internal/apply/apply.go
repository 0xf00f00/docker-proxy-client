// Package apply performs the selector's side-effects: rewriting the byedpi
// (coredns hosts) and sni-spoofing (.ini connect=, fake-sni=) configs, and restarting
// the sni-spoofing container. It is the "apply half" of the old picker — pure
// I/O, no policy. The selector owns WHEN to restart (rate-limit); this owns HOW.
package apply

import (
	"cmp"
	"context"
	"fmt"
	"net/netip"
	"os"
	"regexp"
	"strings"

	"github.com/0xf00f00/cf-edge-manager/internal/decoy"
	"github.com/0xf00f00/cf-edge-manager/internal/dockerapi"
)

type iniKey struct {
	label string
	line  *regexp.Regexp // the whole line, group 1 being everything before the value
	val   *regexp.Regexp // group 1 being the value
}

func newIniKey(label, key, valueChars string) iniKey {
	return iniKey{
		label: label,
		line:  regexp.MustCompile(`(?m)^([ \t]*` + key + `[ \t]*=[ \t]*).*$`),
		val:   regexp.MustCompile(`(?m)^[ \t]*` + key + `[ \t]*=[ \t]*(` + valueChars + `+)`),
	}
}

var (
	connectKey = newIniKey("connect", `connect`, `[^:\s#;]`) // the host part of host:port
	fakeSNIKey = newIniKey("fake-sni", `fake[-_]sni`, `[^\s#;]`)
)

func (k iniKey) get(path string) string {
	b, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	if m := k.val.FindStringSubmatch(string(b)); m != nil {
		return m[1]
	}
	return ""
}

// value is spliced into a regexp replacement: callers must keep `$` and newlines out of it.
func (k iniKey) set(path, value string) error {
	b, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	if !k.line.Match(b) {
		return fmt.Errorf("%s has no %s line", path, k.label)
	}
	return os.WriteFile(path, k.line.ReplaceAll(b, []byte("${1}"+value)), 0o644) //nolint:gosec
}

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
func (a *Applier) CurrentSnispoof() string { return connectKey.get(a.SnispoofConf) }

// WriteByedpi rewrites the coredns hosts file to point the front domain at ip
// (no restart needed — coredns re-reads the hosts file).
func (a *Applier) WriteByedpi(ip string) error {
	if a.FrontHost == "" {
		return fmt.Errorf("FrontHost unset; cannot manage byedpi path")
	}
	if _, err := netip.ParseAddr(ip); err != nil {
		return fmt.Errorf("refusing edge %q: %w", ip, err)
	}
	return os.WriteFile(a.CorednsHosts, []byte(fmt.Sprintf("%s %s\n", ip, a.FrontHost)), 0o644) //nolint:gosec
}

// WriteSnispoof rewrites the .ini connect= line to ip:Port (no restart).
func (a *Applier) WriteSnispoof(ip string) error {
	if _, err := netip.ParseAddr(ip); err != nil {
		return fmt.Errorf("refusing edge %q: %w", ip, err)
	}
	return connectKey.set(a.SnispoofConf, fmt.Sprintf("%s:%d", ip, a.Port))
}

// RestartSnispoof restarts the sni-spoofing container via the docker engine API.
func (a *Applier) RestartSnispoof() error {
	return dockerapi.New(a.DockerSock).Restart(context.Background(), a.SnispoofName)
}

// LiveFakeSNI is the decoy in the sni-spoofing .ini at path, else decoy.Default.
func LiveFakeSNI(path string) string {
	return cmp.Or((&Applier{SnispoofConf: path}).CurrentFakeSNI(), decoy.Default)
}

// CurrentFakeSNI returns the decoy SNI in the sni-spoofing .ini fake-sni= line.
func (a *Applier) CurrentFakeSNI() string { return fakeSNIKey.get(a.SnispoofConf) }

// WriteFakeSNI rewrites the .ini fake-sni= line (no restart).
func (a *Applier) WriteFakeSNI(sni string) error {
	if !decoy.Valid(sni) {
		return fmt.Errorf("refusing invalid decoy %q", sni)
	}
	return fakeSNIKey.set(a.SnispoofConf, sni)
}
