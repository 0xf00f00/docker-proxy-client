// Package pool reads the ranked edge pool the discovery scan publishes
// (pool.txt, best-first, one IP per line). The selector consumes it; keeping it
// a tiny file-reader preserves the existing on-disk contract the dashboard and
// fallback chain already depend on.
package pool

import (
	"os"
	"strings"
)

// Read returns the pool IPs best-first, skipping blanks and # comments. A
// missing/unreadable file yields an empty slice (the selector then keeps the
// current edges rather than churning).
func Read(path string) []string {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	var out []string
	for _, ln := range strings.Split(string(b), "\n") {
		s := strings.TrimSpace(ln)
		if s != "" && !strings.HasPrefix(s, "#") {
			out = append(out, s)
		}
	}
	return out
}
