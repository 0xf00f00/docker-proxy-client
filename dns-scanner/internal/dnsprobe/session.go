package dnsprobe

import (
	"net"
	"strconv"
	"time"
)

// Session is one connected UDP socket to a resolver, reused across all of that
// resolver's funnel stages (serially). bindIP pins the egress source.
type Session struct {
	conn    net.Conn
	timeout time.Duration
}

// Dial opens a connected UDP socket to ip:port. bindIP, when set, pins the
// local source address (the container's direct_internet address in prod).
func Dial(ip string, port int, bindIP string, timeout time.Duration) (*Session, error) {
	d := net.Dialer{Timeout: timeout}
	if bindIP != "" {
		d.LocalAddr = &net.UDPAddr{IP: net.ParseIP(bindIP)}
	}
	conn, err := d.Dial("udp", net.JoinHostPort(ip, strconv.Itoa(port)))
	if err != nil {
		return nil, err
	}
	return &Session{conn: conn, timeout: timeout}, nil
}

// Query writes pkt and reads replies until one matches id or the deadline
// passes. Mismatched (stale/duplicate) datagrams are skipped.
func (s *Session) Query(pkt []byte, id uint16) (*Response, error) {
	deadline := time.Now().Add(s.timeout)
	if err := s.conn.SetDeadline(deadline); err != nil {
		return nil, err
	}
	if _, err := s.conn.Write(pkt); err != nil {
		return nil, err
	}
	buf := make([]byte, 4096)
	for {
		n, err := s.conn.Read(buf)
		if err != nil {
			return nil, err
		}
		resp, perr := ParseResponse(buf[:n], id)
		if perr == nil {
			return resp, nil
		}
		if time.Now().After(deadline) {
			return nil, perr
		}
	}
}

func (s *Session) Close() error { return s.conn.Close() }
