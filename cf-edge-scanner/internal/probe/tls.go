// Package probe holds network checks that are not cfst's job.
package probe

import (
	"context"
	"crypto/tls"
	"fmt"
	"net"
	"net/netip"
	"strings"
	"time"
)

// TLSSurvives opens a TLS session to ip:port with the given SNI, holds it idle
// (many DPI boxes reset idle encrypted sessions), then sends one /cdn-cgi/trace
// request and requires a valid Cloudflare trace ("fl=") back. A clean TCP ping
// only proves the edge answers; this proves a real encrypted session lives.
//
// The cert is intentionally not verified: we dial a bare edge IP with a fronted
// SNI, so identity is irrelevant — only session survival matters. ~1 KB total.
func TLSSurvives(ctx context.Context, ip netip.Addr, port int, sni string, hold time.Duration) (bool, error) {
	dialer := tls.Dialer{
		Config: &tls.Config{
			ServerName:         sni,
			InsecureSkipVerify: true, //nolint:gosec // fronted SNI on a raw IP; survival, not identity
			MinVersion:         tls.VersionTLS12,
		},
	}
	addr := net.JoinHostPort(ip.String(), fmt.Sprintf("%d", port))
	conn, err := dialer.DialContext(ctx, "tcp", addr)
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

	buf := make([]byte, 4096)
	var resp strings.Builder
	for {
		n, err := conn.Read(buf)
		if n > 0 {
			resp.Write(buf[:n])
			if strings.Contains(resp.String(), "fl=") {
				return true, nil
			}
		}
		if err != nil {
			return strings.Contains(resp.String(), "fl="), nil
		}
	}
}
