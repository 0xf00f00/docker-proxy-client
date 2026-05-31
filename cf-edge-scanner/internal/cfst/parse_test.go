package cfst

import (
	"net/netip"
	"os"
	"path/filepath"
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

func TestParseEdgesEmpty(t *testing.T) {
	edges, err := parseEdges(strings.NewReader("IP,Sent,Received,Loss,Latency,Speed\n"))
	if err != nil {
		t.Fatalf("parseEdges: %v", err)
	}
	if len(edges) != 0 {
		t.Fatalf("want 0 edges, got %d", len(edges))
	}
}

func TestReadCIDRs(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "ranges.txt")
	content := "# comment line\n104.16.0.0/12\n172.64.0.0/13  # inline comment\n\nnot-a-cidr\n"
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}

	cidrs, err := readCIDRs(path)
	if err != nil {
		t.Fatalf("readCIDRs: %v", err)
	}
	want := []string{"104.16.0.0/12", "172.64.0.0/13"}
	if len(cidrs) != len(want) {
		t.Fatalf("want %v, got %v", want, cidrs)
	}
	for i := range want {
		if cidrs[i] != want[i] {
			t.Errorf("cidr[%d] = %q, want %q", i, cidrs[i], want[i])
		}
	}
}
