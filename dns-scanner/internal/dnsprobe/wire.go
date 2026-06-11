// Package dnsprobe builds and parses raw DNS messages and manages one connected
// UDP socket per resolver (reused across the funnel's stages). No external deps.
package dnsprobe

import (
	"crypto/rand"
	"encoding/binary"
	"errors"
	"strings"
)

// Record types and rcodes used by the funnel.
const (
	TypeA   uint16 = 1
	TypeNS  uint16 = 2
	TypeTXT uint16 = 16
	typeOPT uint16 = 41

	RcodeNoError  = 0
	RcodeFormErr  = 1
	RcodeNXDomain = 3
)

// Response is a light parse of a DNS reply — enough for the funnel's gates.
type Response struct {
	ID          uint16
	QR          bool
	Rcode       int
	AnCount     int
	EDNSPayload int // UDP payload size echoed in the response OPT (0 if none)
}

// BuildQuery builds a query for name/qtype. When ednsBufSize > 0 it appends an
// EDNS0 OPT record advertising that UDP payload size. Returns the wire bytes and
// the random transaction id to match the reply against.
func BuildQuery(name string, qtype, ednsBufSize uint16) ([]byte, uint16, error) {
	var idb [2]byte
	_, _ = rand.Read(idb[:])
	id := binary.BigEndian.Uint16(idb[:])

	arcount := uint16(0)
	if ednsBufSize > 0 {
		arcount = 1
	}
	hdr := make([]byte, 12)
	binary.BigEndian.PutUint16(hdr[0:], id)
	binary.BigEndian.PutUint16(hdr[2:], 0x0100) // RD
	binary.BigEndian.PutUint16(hdr[4:], 1)      // QDCOUNT
	binary.BigEndian.PutUint16(hdr[10:], arcount)

	qname, err := encodeName(name)
	if err != nil {
		return nil, 0, err
	}
	out := append([]byte{}, hdr...)
	out = append(out, qname...)
	tail := make([]byte, 4)
	binary.BigEndian.PutUint16(tail[0:], qtype)
	binary.BigEndian.PutUint16(tail[2:], 1) // class IN
	out = append(out, tail...)

	if ednsBufSize > 0 {
		opt := make([]byte, 11)
		opt[0] = 0x00                                    // root name
		binary.BigEndian.PutUint16(opt[1:], typeOPT)     // type OPT
		binary.BigEndian.PutUint16(opt[3:], ednsBufSize) // class = UDP payload size
		// ttl (opt[5:9]) and rdlen (opt[9:11]) stay zero
		out = append(out, opt...)
	}
	return out, id, nil
}

// ParseResponse validates the header against id and extracts rcode/ancount and
// the echoed EDNS payload size. Returns an error on id/QR mismatch (stale reply).
func ParseResponse(buf []byte, id uint16) (*Response, error) {
	if len(buf) < 12 {
		return nil, errors.New("short message")
	}
	r := &Response{
		ID:      binary.BigEndian.Uint16(buf[0:2]),
		QR:      buf[2]&0x80 != 0,
		Rcode:   int(buf[3] & 0x0f),
		AnCount: int(binary.BigEndian.Uint16(buf[6:8])),
	}
	if r.ID != id || !r.QR {
		return nil, errors.New("id/qr mismatch")
	}
	r.EDNSPayload = scanOptUDPSize(buf)
	return r, nil
}

func encodeName(name string) ([]byte, error) {
	name = strings.TrimSuffix(name, ".")
	var out []byte
	if name != "" {
		for _, label := range strings.Split(name, ".") {
			if len(label) == 0 || len(label) > 63 {
				return nil, errors.New("invalid label length")
			}
			out = append(out, byte(len(label)))
			out = append(out, label...)
		}
	}
	out = append(out, 0x00)
	if len(out) > 255 {
		return nil, errors.New("name too long")
	}
	return out, nil
}

// scanOptUDPSize walks the message to the additional section and returns the
// OPT record's advertised UDP size (its CLASS field). 0 if absent/malformed.
func scanOptUDPSize(buf []byte) int {
	if len(buf) < 12 {
		return 0
	}
	qd := int(binary.BigEndian.Uint16(buf[4:6]))
	an := int(binary.BigEndian.Uint16(buf[6:8]))
	ns := int(binary.BigEndian.Uint16(buf[8:10]))
	ar := int(binary.BigEndian.Uint16(buf[10:12]))
	off := 12

	var err error
	for i := 0; i < qd; i++ {
		if off, err = skipName(buf, off); err != nil {
			return 0
		}
		off += 4 // qtype + qclass
		if off > len(buf) {
			return 0
		}
	}
	for i := 0; i < an+ns; i++ {
		if off, err = skipRR(buf, off); err != nil {
			return 0
		}
	}
	for i := 0; i < ar; i++ {
		noff, err := skipName(buf, off)
		if err != nil || noff+10 > len(buf) {
			return 0
		}
		rtype := binary.BigEndian.Uint16(buf[noff:])
		cls := binary.BigEndian.Uint16(buf[noff+2:])
		rdlen := int(binary.BigEndian.Uint16(buf[noff+8:]))
		if rtype == typeOPT {
			return int(cls)
		}
		off = noff + 10 + rdlen
		if off > len(buf) {
			return 0
		}
	}
	return 0
}

func skipName(buf []byte, off int) (int, error) {
	for {
		if off >= len(buf) {
			return 0, errors.New("name oob")
		}
		b := buf[off]
		if b == 0 {
			return off + 1, nil
		}
		if b&0xC0 == 0xC0 { // compression pointer ends the name (2 bytes)
			return off + 2, nil
		}
		off += int(b) + 1
	}
}

func skipRR(buf []byte, off int) (int, error) {
	off, err := skipName(buf, off)
	if err != nil || off+10 > len(buf) {
		return 0, errors.New("rr oob")
	}
	rdlen := int(binary.BigEndian.Uint16(buf[off+8:]))
	off += 10 + rdlen
	if off > len(buf) {
		return 0, errors.New("rr oob")
	}
	return off, nil
}

// RandLabel returns n random DNS-safe lowercase-alnum characters.
func RandLabel(n int) string {
	const cs = "abcdefghijklmnopqrstuvwxyz0123456789"
	buf := make([]byte, n)
	_, _ = rand.Read(buf)
	for i := range buf {
		buf[i] = cs[int(buf[i])%len(cs)]
	}
	return string(buf)
}

// RandLabels returns ~nChars of random data as dotted labels (each ≤63 chars),
// for the upload-carriage stage's long QNAME.
func RandLabels(nChars int) string {
	var parts []string
	for nChars > 0 {
		l := min(nChars, 63)
		parts = append(parts, RandLabel(l))
		nChars -= l
	}
	return strings.Join(parts, ".")
}
