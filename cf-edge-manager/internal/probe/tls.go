// Package probe holds network checks that are not cfst's job.
package probe

import (
	"bufio"
	"context"
	"crypto/tls"
	"fmt"
	"net"
	"net/http"
	"net/netip"
	"regexp"
	"strings"
	"sync"
	"time"

	utls "github.com/refraction-networking/utls"
	"sni-spoofing-go/packet"
)

// TLSSurvives opens a TLS session to ip:port with the given fronted SNI, holds
// it idle (many DPI boxes reset idle encrypted sessions), then sends one
// GET /cdn-cgi/trace and requires a genuine Cloudflare trace back (HTTP 2xx
// whose body carries a parseable `ip=` field). A clean TCP ping only proves the
// edge answers; this proves a real encrypted session lived AND landed on a CF
// edge that served our zone path with the decoy SNI intact.
//
// Why the trace and not "any HTTP status": a reset/timeout yields no response at
// all (the survival failure we hunt), but a 403 challenge or a wrong-host reply
// can still emit an "HTTP/" line — accepting those false-passes the gate. The
// edge serves /cdn-cgi/trace before the origin regardless of zone, so a healthy
// decoy-fronted session returns 200 + a trace; requiring the `ip=` field is the
// fake-SNI gate done right.
//
// utlsName selects the ClientHello fingerprint so the gate's handshake matches
// what production injects (e.g. "firefox"); "" or "none" falls back to the Go
// stdlib hello. The cert is intentionally not verified: we dial a bare edge IP
// with a fronted SNI, so identity is irrelevant — only session survival matters.
func TLSSurvives(ctx context.Context, ip netip.Addr, port int, sni, utlsName string, hold time.Duration) (bool, error) {
	addr := net.JoinHostPort(ip.String(), fmt.Sprintf("%d", port))
	conn, err := dialFakeSNI(ctx, addr, sni, utlsName)
	if err != nil {
		return false, err
	}
	defer func() { _ = conn.Close() }()

	if dl, ok := ctx.Deadline(); ok {
		_ = conn.SetDeadline(dl)
	}

	select {
	case <-ctx.Done():
		return false, ctx.Err()
	case <-time.After(hold):
	}

	req := fmt.Sprintf("GET /cdn-cgi/trace HTTP/1.1\r\nHost: %s\r\nUser-Agent: curl\r\nConnection: close\r\n\r\n", sni)
	if _, err := conn.Write([]byte(req)); err != nil {
		return false, err
	}

	resp, err := http.ReadResponse(bufio.NewReader(conn), nil)
	if err != nil {
		return false, err
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return false, fmt.Errorf("unexpected HTTP status %s", resp.Status)
	}
	body := make([]byte, 0, 1024)
	buf := make([]byte, 1024)
	for len(body) < 1<<16 {
		n, rerr := resp.Body.Read(buf)
		body = append(body, buf[:n]...)
		if rerr != nil {
			break
		}
	}
	if _, perr := parseCloudflareTraceIP(string(body)); perr != nil {
		return false, perr
	}
	return true, nil
}

// dialFakeSNI opens a TLS connection to addr with ServerName=sni, using a uTLS
// fingerprinted ClientHello when utlsName names a browser preset (so the gate's
// handshake looks like production's), and the Go stdlib hello otherwise.
func dialFakeSNI(ctx context.Context, addr, sni, utlsName string) (net.Conn, error) {
	raw, err := (&net.Dialer{}).DialContext(ctx, "tcp", addr)
	if err != nil {
		return nil, err
	}
	if dl, ok := ctx.Deadline(); ok {
		_ = raw.SetDeadline(dl)
	}

	// "none"/"" select the legacy template, which has no uTLS preset — use the
	// stdlib hello rather than failing the gate over a fingerprint choice.
	if strings.TrimSpace(utlsName) == "" || packet.IsLegacyUTLS(utlsName) {
		c := tls.Client(raw, &tls.Config{
			ServerName:         sni,
			InsecureSkipVerify: true, //nolint:gosec // fronted SNI on a raw IP; survival, not identity
			MinVersion:         tls.VersionTLS12,
		})
		if err := c.HandshakeContext(ctx); err != nil {
			_ = raw.Close()
			return nil, err
		}
		return c, nil
	}

	id, err := packet.ParseClientHelloID(utlsName)
	if err != nil {
		_ = raw.Close()
		return nil, fmt.Errorf("utls %q: %w", utlsName, err)
	}
	uc := utls.UClient(raw, &utls.Config{
		ServerName:         sni,
		InsecureSkipVerify: true, //nolint:gosec // fronted SNI on a raw IP; survival, not identity
		MinVersion:         utls.VersionTLS12,
	}, id)
	if err := uc.HandshakeContext(ctx); err != nil {
		_ = raw.Close()
		return nil, err
	}
	return uc, nil
}

var traceIPPattern = regexp.MustCompile(`(?m)^ip=([0-9.]+)\s*$`)

// parseCloudflareTraceIP extracts the `ip=` field from a /cdn-cgi/trace body and
// requires it to be a valid IPv4 — proof the session reached a genuine CF edge.
func parseCloudflareTraceIP(body string) (string, error) {
	m := traceIPPattern.FindStringSubmatch(body)
	if len(m) != 2 {
		return "", fmt.Errorf("trace response has no ip field")
	}
	ip := strings.TrimSpace(m[1])
	if net.ParseIP(ip).To4() == nil {
		return "", fmt.Errorf("invalid trace ip %q", ip)
	}
	return ip, nil
}

// BurstSurvival fires `burst` fake-SNI TLS sessions to ip:port concurrently and
// reports the fraction that failed to carry a live session, plus one sample
// error for logging. This is the signal a single handshake misses: under
// censorship the per-edge failure that breaks real use only surfaces when many
// connections to one IP are opened at once (a docker pull, an xHTTP download) —
// the DPI resets a fraction of a concurrent burst while a lone probe sails
// through. tcp_loss and a single TLSSurvives both report "clean" on an edge that
// drops 1-in-7 under load; the burst rate is what discriminates a 0%-loss edge
// from a 15%-loss one. Cheap (~1 KB/conn) and bounded by `hold` in wall time.
func BurstSurvival(ctx context.Context, ip netip.Addr, port int, sni, utlsName string, hold time.Duration, burst int) (float64, error) {
	if burst < 1 {
		burst = 1
	}
	var (
		wg     sync.WaitGroup
		mu     sync.Mutex
		failed int
		sample error
	)
	for range burst {
		wg.Add(1)
		go func() {
			defer wg.Done()
			ok, err := TLSSurvives(ctx, ip, port, sni, utlsName, hold)
			if ok {
				return
			}
			mu.Lock()
			failed++
			if sample == nil && err != nil {
				sample = err
			}
			mu.Unlock()
		}()
	}
	wg.Wait()
	return float64(failed) / float64(burst), sample
}
