// Package assign writes the working resolver set into client_resolvers.txt
// (only a scanner-managed block, preserving human-added entries), atomically,
// and triggers a configurable reload only when the set actually changed.
package assign

import (
	"context"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

const (
	beginMarker = "# >>> mdns-scanner managed (do not edit this block) >>>"
	endMarker   = "# <<< mdns-scanner managed <<<"

	// reloadTimeout bounds the mdns restart call (Docker's stop grace + start).
	reloadTimeout = 30 * time.Second
)

// ReadManaged returns the IPs currently in the scanner-managed block.
func ReadManaged(path string) []string {
	_, managed := splitManaged(path)
	return managed
}

func ReadAll(path string) []string {
	human, managed := splitManaged(path)
	seen := make(map[string]struct{})
	var out []string
	add := func(ip string) {
		if net.ParseIP(ip) == nil {
			return
		}
		if _, dup := seen[ip]; dup {
			return
		}
		seen[ip] = struct{}{}
		out = append(out, ip)
	}
	for _, line := range strings.Split(human, "\n") {
		add(strings.TrimSpace(line))
	}
	for _, ip := range managed {
		add(ip)
	}
	return out
}

// WriteManaged replaces the scanner-managed block in path with ips, preserving
// every line a human kept outside the markers. Atomic (temp + rename). Reports
// whether the managed set changed (so the caller can debounce the reload).
//
// An empty ips is a deliberate no-op: we never clobber the last-known-good set
// with nothing just because a scan came up empty — a stale resolver that might
// recover beats none, and mdns auto-disables dead entries itself.
func WriteManaged(path string, ips []string) (changed bool, err error) {
	if len(ips) == 0 {
		return false, nil
	}
	human, oldManaged := splitManaged(path)

	// A resolver the user kept outside the block stays theirs: if the scanner also
	// verified it, leave it as their line and keep it out of the managed block so
	// the file never lists the same IP twice. (It still counts toward the target —
	// it was verified this cycle — it just isn't re-homed into the managed block.)
	if humanIPs := parseIPSet(human); len(humanIPs) > 0 {
		kept := make([]string, 0, len(ips))
		for _, ip := range ips {
			if _, isHuman := humanIPs[ip]; !isHuman {
				kept = append(kept, ip)
			}
		}
		ips = kept
	}
	if len(ips) == 0 {
		return false, nil // everything working is already a human line — nothing to manage
	}
	if equalSet(oldManaged, ips) {
		return false, nil
	}

	var b strings.Builder
	if h := strings.TrimRight(human, "\n"); h != "" {
		b.WriteString(h)
		b.WriteString("\n\n")
	}
	b.WriteString(beginMarker + "\n")
	for _, ip := range ips {
		b.WriteString(ip + "\n")
	}
	b.WriteString(endMarker + "\n")

	return true, atomicWrite(path, []byte(b.String()))
}

// Reload triggers the mdns reload by POSTing to url — the docker-socket-proxy
// container-restart endpoint. Empty = no-op. Called only when WriteManaged
// reported a change. A native HTTP call (no shell, no curl dependency) keeps the
// reload free of injection surface and testable.
func Reload(url string) error {
	url = strings.TrimSpace(url)
	if url == "" {
		return nil
	}
	ctx, cancel := context.WithTimeout(context.Background(), reloadTimeout)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, nil)
	if err != nil {
		return fmt.Errorf("reload %q: %w", url, err)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return fmt.Errorf("reload %q: %w", url, err)
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode >= 300 {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
		return fmt.Errorf("reload %q: %s: %s", url, resp.Status, strings.TrimSpace(string(body)))
	}
	return nil
}

func splitManaged(path string) (human string, managed []string) {
	data, err := os.ReadFile(path)
	if err != nil {
		return "", nil
	}
	var humanLines []string
	in := false
	for _, line := range strings.Split(string(data), "\n") {
		t := strings.TrimSpace(line)
		switch {
		case t == beginMarker:
			in = true
		case t == endMarker:
			in = false
		case in:
			if t != "" && !strings.HasPrefix(t, "#") {
				managed = append(managed, t)
			}
		default:
			humanLines = append(humanLines, line)
		}
	}
	return strings.Join(humanLines, "\n"), managed
}

// parseIPSet collects the valid IPs from a newline-joined block (human lines may
// mix in comments and blanks, which are skipped).
func parseIPSet(block string) map[string]struct{} {
	out := make(map[string]struct{})
	for _, line := range strings.Split(block, "\n") {
		ip := strings.TrimSpace(line)
		if net.ParseIP(ip) != nil {
			out[ip] = struct{}{}
		}
	}
	return out
}

func equalSet(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	as := append([]string(nil), a...)
	bs := append([]string(nil), b...)
	sort.Strings(as)
	sort.Strings(bs)
	for i := range as {
		if as[i] != bs[i] {
			return false
		}
	}
	return true
}

func atomicWrite(path string, data []byte) error {
	dir := filepath.Dir(path)
	tmp, err := os.CreateTemp(dir, ".resolvers-*.tmp")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	defer func() { _ = os.Remove(tmpName) }()
	if _, err := tmp.Write(data); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmpName, path)
}
