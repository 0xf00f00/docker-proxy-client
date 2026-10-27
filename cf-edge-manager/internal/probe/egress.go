package probe

import "net"

// DefaultInterfaceIPv4 returns the local source IPv4 the kernel would use to reach remoteAddr (UDP
// connect, no packet sent), or "" on failure. It asserts probes egress on eth0 and not the VPN default
// route, where a survival probe would measure the wrong path and false-fail.
func DefaultInterfaceIPv4(remoteAddr string) string {
	if remoteAddr == "" {
		remoteAddr = "8.8.8.8"
	}
	conn, err := net.Dial("udp4", net.JoinHostPort(remoteAddr, "53"))
	if err != nil {
		return ""
	}
	defer func() { _ = conn.Close() }()
	if ua, ok := conn.LocalAddr().(*net.UDPAddr); ok {
		return ua.IP.String()
	}
	return ""
}
