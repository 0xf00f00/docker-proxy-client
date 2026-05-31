package cfst

import (
	"bufio"
	"net/netip"
	"os"
	"strings"
)

// readCIDRs returns the canonical CIDRs from a ranges file, ignoring blank
// lines and "#" comments (inline or whole-line).
func readCIDRs(path string) ([]string, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	var cidrs []string
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := sc.Text()
		if i := strings.IndexByte(line, '#'); i >= 0 {
			line = line[:i]
		}
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		if p, err := netip.ParsePrefix(line); err == nil {
			cidrs = append(cidrs, p.String())
		}
	}
	if err := sc.Err(); err != nil {
		return nil, err
	}
	return cidrs, nil
}
