package cfst

import (
	"encoding/csv"
	"io"
	"net/netip"
	"strconv"
	"strings"
)

// Stats is one IP's reachability measurement.
type Stats struct {
	Sent      int     `json:"sent"`
	Received  int     `json:"received"`
	Loss      float64 `json:"loss"`
	LatencyMS float64 `json:"latency_ms"`
}

// Edge pairs an IP with its measured Stats. cfst emits rows already sorted
// best-first; parseEdges preserves that order.
type Edge struct {
	IP netip.Addr `json:"ip"`
	Stats
}

// parseEdges reads cfst's result CSV. The layout is positional —
// IP,Sent,Received,LossRate,AvgLatency,Speed — with a localized header row we
// skip by position rather than by name. Rows whose first field is not an IP, or
// that are short/garbled, are dropped (cfst can write a partial file on a
// non-zero exit, which we still want to salvage).
func parseEdges(r io.Reader) ([]Edge, error) {
	cr := csv.NewReader(r)
	cr.FieldsPerRecord = -1
	cr.TrimLeadingSpace = true

	records, err := cr.ReadAll()
	if err != nil {
		return nil, err
	}

	edges := make([]Edge, 0, len(records))
	for i, rec := range records {
		if i == 0 {
			continue // header
		}
		e, ok := edgeFromRecord(rec)
		if ok {
			edges = append(edges, e)
		}
	}
	return edges, nil
}

func edgeFromRecord(rec []string) (Edge, bool) {
	if len(rec) < 5 {
		return Edge{}, false
	}
	ip, err := netip.ParseAddr(strings.TrimSpace(rec[0]))
	if err != nil {
		return Edge{}, false
	}
	return Edge{
		IP: ip,
		Stats: Stats{
			Sent:      atoi(rec[1]),
			Received:  atoi(rec[2]),
			Loss:      atof(rec[3]),
			LatencyMS: atof(rec[4]),
		},
	}, true
}

func atoi(s string) int {
	n, _ := strconv.Atoi(strings.TrimSpace(s))
	return n
}

func atof(s string) float64 {
	f, _ := strconv.ParseFloat(strings.TrimSpace(s), 64)
	return f
}
