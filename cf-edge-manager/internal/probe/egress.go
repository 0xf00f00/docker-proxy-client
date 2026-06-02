package probe

import "net"

// DefaultInterfaceIPv4 returns the local IPv4 source address the kernel would
// use to reach remoteAddr, via the UDP-connect trick: connecting a UDP socket
// reveals the chosen source IP without sending a single packet. Returns "" if
// the lookup fails. Borrowed from sni-spoofing-go's network helper.
//
// We use it to assert the probe path egresses on the macvlan eth0 IP and not the
// VPN default route — a survival probe that leaks onto the tunnel measures the
// wrong path and false-fails (see the probe-fidelity rule: probe from eth0).
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
