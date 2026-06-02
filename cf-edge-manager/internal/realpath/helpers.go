package realpath

import (
	"net"
	"strings"
	"time"
)

func errStr(e error) string {
	if e == nil {
		return ""
	}
	return e.Error()
}

// waitDial polls until addr accepts a TCP connection or the deadline passes.
func waitDial(addr string, deadline time.Time) bool {
	for time.Now().Before(deadline) {
		c, err := net.DialTimeout("tcp", addr, time.Second)
		if err == nil {
			_ = c.Close()
			return true
		}
		time.Sleep(250 * time.Millisecond)
	}
	return false
}

// splitURL turns http://host[:port]/path into (host:port, /path) for a raw
// socks dial. Defaults to :80 and "/".
func splitURL(u string) (hostport, path string) {
	s := strings.TrimPrefix(strings.TrimPrefix(u, "https://"), "http://")
	if i := strings.IndexByte(s, '/'); i >= 0 {
		return ensurePort(s[:i]), s[i:]
	}
	return ensurePort(s), "/"
}

func ensurePort(h string) string {
	if strings.Contains(h, ":") {
		return h
	}
	return h + ":80"
}

// dohTarget turns https://host[:port]/path?q into (host:port, /path?q) for a raw
// socks dial + TLS, defaulting to :443 and "/dns-query".
func dohTarget(u string) (hostport, path string) {
	s := strings.TrimPrefix(strings.TrimPrefix(u, "https://"), "http://")
	if i := strings.IndexByte(s, '/'); i >= 0 {
		return ensureTLSPort(s[:i]), s[i:]
	}
	return ensureTLSPort(s), "/dns-query"
}

func ensureTLSPort(h string) string {
	if strings.Contains(h, ":") {
		return h
	}
	return h + ":443"
}

func hostOnly(hostport string) string {
	if h, _, err := net.SplitHostPort(hostport); err == nil {
		return h
	}
	return hostport
}
