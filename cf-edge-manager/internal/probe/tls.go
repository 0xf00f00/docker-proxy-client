// Package probe holds network checks that are not cfst's job.
package probe

import (
	"context"
	"crypto/tls"
	"fmt"
	"net"
	"sni-spoofing-go/packet"
	"strings"

	utls "github.com/refraction-networking/utls"
)

// TLSHello reports whether a TLS handshake naming sni completes against ip:port.
func TLSHello(ctx context.Context, ip string, port int, sni, utlsName string) bool {
	conn, err := dialFakeSNI(ctx, net.JoinHostPort(ip, fmt.Sprintf("%d", port)), sni, utlsName)
	if err != nil {
		return false
	}
	_ = conn.Close()
	return true
}

// dialFakeSNI opens a TLS connection to addr with ServerName=sni, using a uTLS
// fingerprinted ClientHello when utlsName names a browser preset (so the
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
	// stdlib hello rather than failing over a fingerprint choice.
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
