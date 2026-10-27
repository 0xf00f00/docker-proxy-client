package cfst

import (
	"net/netip"
	"strings"
	"testing"
)

func TestParseEdges(t *testing.T) {
	// Header (localized, ignored by position) + two valid rows + one garbled row.
	in := strings.Join([]string{
		"IP,Sent,Received,Loss,Latency,Speed",
		"104.16.0.1,10,10,0.00,42.5,0.00",
		"172.64.0.1,10,9,0.10,88.0,0.00",
		"not-an-ip,10,0,1.00,0,0",
	}, "\n")

	edges, err := parseEdges(strings.NewReader(in))
	if err != nil {
		t.Fatalf("parseEdges: %v", err)
	}
	if len(edges) != 2 {
		t.Fatalf("want 2 edges, got %d: %+v", len(edges), edges)
	}
	if edges[0].IP != netip.MustParseAddr("104.16.0.1") {
		t.Errorf("first IP = %v", edges[0].IP)
	}
	if edges[0].Sent != 10 || edges[0].Received != 10 || edges[0].Loss != 0 || edges[0].LatencyMS != 42.5 {
		t.Errorf("first stats = %+v", edges[0].Stats)
	}
	if edges[1].Loss != 0.10 || edges[1].Received != 9 {
		t.Errorf("second stats = %+v", edges[1].Stats)
	}
}
