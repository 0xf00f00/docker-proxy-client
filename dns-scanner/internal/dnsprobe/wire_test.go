package dnsprobe

import (
	"strings"
	"testing"
)

// flipToResponse turns a query produced by BuildQuery into a parseable reply by
// setting the QR bit and rcode, without touching the rest of the wire layout.
func flipToResponse(pkt []byte, rcode byte) []byte {
	out := append([]byte(nil), pkt...)
	out[2] |= 0x80 // QR = 1
	out[3] = (out[3] &^ 0x0f) | (rcode & 0x0f)
	return out
}

func TestBuildParseRoundTrip(t *testing.T) {
	pkt, id, err := BuildQuery("x.example.com", TypeA, 0)
	if err != nil {
		t.Fatalf("BuildQuery: %v", err)
	}
	resp, err := ParseResponse(flipToResponse(pkt, RcodeNXDomain), id)
	if err != nil {
		t.Fatalf("ParseResponse: %v", err)
	}
	if !resp.QR {
		t.Error("QR not set")
	}
	if resp.ID != id {
		t.Errorf("ID = %d, want %d", resp.ID, id)
	}
	if resp.Rcode != RcodeNXDomain {
		t.Errorf("Rcode = %d, want %d", resp.Rcode, RcodeNXDomain)
	}
}

func TestParseResponseRejectsMismatch(t *testing.T) {
	pkt, id, _ := BuildQuery("a.b", TypeA, 0)
	resp := flipToResponse(pkt, RcodeNoError)
	if _, err := ParseResponse(resp, id+1); err == nil {
		t.Error("want error on id mismatch, got nil")
	}
	if _, err := ParseResponse(pkt, id); err == nil {
		t.Error("want error when QR unset (a query, not a reply)")
	}
	if _, err := ParseResponse([]byte{1, 2, 3}, id); err == nil {
		t.Error("want error on short message")
	}
}

// EDNS payload size advertised in the OPT record round-trips through the parser.
func TestParseResponseExtractsEDNS(t *testing.T) {
	const size = 1232
	pkt, id, err := BuildQuery("q.example.com", TypeTXT, size)
	if err != nil {
		t.Fatalf("BuildQuery: %v", err)
	}
	resp, err := ParseResponse(flipToResponse(pkt, RcodeNoError), id)
	if err != nil {
		t.Fatalf("ParseResponse: %v", err)
	}
	if resp.EDNSPayload != size {
		t.Errorf("EDNSPayload = %d, want %d", resp.EDNSPayload, size)
	}
}

func TestBuildQueryRejectsBadNames(t *testing.T) {
	cases := []string{
		strings.Repeat("a", 64) + ".com", // label > 63
		"a..b",                           // empty label
		strings.Repeat("a.", 200) + "a",  // total name > 255
	}
	for _, name := range cases {
		if _, _, err := BuildQuery(name, TypeA, 0); err == nil {
			t.Errorf("BuildQuery(%q) = nil err, want rejection", name)
		}
	}
	// Root name is valid.
	if _, _, err := BuildQuery("", TypeA, 0); err != nil {
		t.Errorf("BuildQuery(root) = %v, want ok", err)
	}
}

func TestRandLabelsStayWithinLabelLimit(t *testing.T) {
	got := RandLabels(200)
	for _, label := range strings.Split(got, ".") {
		if len(label) == 0 || len(label) > 63 {
			t.Fatalf("label %q has invalid length %d", label, len(label))
		}
	}
	// The whole thing must still encode (every label <= 63, used as a QNAME).
	if _, _, err := BuildQuery(got+".example.com", TypeTXT, 0); err != nil {
		t.Fatalf("RandLabels output did not encode: %v", err)
	}
}

// ParseResponse must never panic on adversarial input — it parses untrusted
// bytes straight off a hostile network.
func FuzzParseResponse(f *testing.F) {
	for _, seed := range [][]byte{
		{},
		{0x00},
		make([]byte, 12),
		{0, 1, 0x80, 0, 0, 1, 0, 1, 0, 0, 0, 1, 0xC0, 0x0C}, // compression pointer
	} {
		f.Add(seed, uint16(1))
	}
	if pkt, id, err := BuildQuery("a.example.com", TypeTXT, 1232); err == nil {
		f.Add(flipToResponse(pkt, RcodeNoError), id)
	}
	f.Fuzz(func(t *testing.T, data []byte, id uint16) {
		resp, err := ParseResponse(data, id) // must not panic
		if err == nil && resp == nil {
			t.Fatal("nil error but nil response")
		}
	})
}
