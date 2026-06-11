// Package targets loads the tiered candidate IP lists (Iran-first) from the data
// directory and yields them in a deterministic, resumable order. Per-/24 spacing
// is enforced downstream by the pacer, so ordering here is a seeded shuffle.
package targets

import (
	"bufio"
	"encoding/binary"
	"math/rand"
	"net"
	"os"
	"path/filepath"
	"strings"
)

// Tier is a named, ordered block of candidate IPs. Tiers are tried in order;
// scanning stops as soon as enough working resolvers are found.
type Tier struct {
	Name string
	IPs  []string
}

// Load builds the tiers from dataDir:
//
//	A "seed"   — white_dns_iran.txt + ir-resolvers.txt (pre-vetted, scanned first)
//	B "ir-cidr"— sampled from ir-cidrs.txt (only if the seed runs short)
//	C "public" — public_resolvers.txt (last resort)
//
// seed makes the shuffle/sampling deterministic so a resumed scan maps to the
// same IPs. cidrSampleMax bounds how many IPs the CIDR tier contributes.
func Load(dataDir string, seed int64, cidrSampleMax int) ([]Tier, error) {
	rng := rand.New(rand.NewSource(seed))
	seen := make(map[string]struct{})

	seedIPs, err := readIPs(seen, rng,
		filepath.Join(dataDir, "white_dns_iran.txt"),
		filepath.Join(dataDir, "ir-resolvers.txt"),
	)
	if err != nil {
		return nil, err
	}

	cidrIPs, err := sampleCIDRs(filepath.Join(dataDir, "ir-cidrs.txt"), cidrSampleMax, seen, rng)
	if err != nil {
		return nil, err
	}

	publicIPs, err := readIPs(seen, rng, filepath.Join(dataDir, "public_resolvers.txt"))
	if err != nil {
		return nil, err
	}

	return []Tier{
		{Name: "seed", IPs: seedIPs},
		{Name: "ir-cidr", IPs: cidrIPs},
		{Name: "public", IPs: publicIPs},
	}, nil
}

// readIPs reads one-IP-per-line files, dedups against seen, and shuffles.
func readIPs(seen map[string]struct{}, rng *rand.Rand, paths ...string) ([]string, error) {
	var ips []string
	for _, p := range paths {
		lines, err := readLines(p)
		if err != nil {
			return nil, err
		}
		for _, l := range lines {
			if net.ParseIP(l) == nil {
				continue
			}
			if _, dup := seen[l]; dup {
				continue
			}
			seen[l] = struct{}{}
			ips = append(ips, l)
		}
	}
	rng.Shuffle(len(ips), func(i, j int) { ips[i], ips[j] = ips[j], ips[i] })
	return ips, nil
}

// sampleCIDRs takes a handful of IPs from each CIDR up to maxIPs, deduped and shuffled.
func sampleCIDRs(path string, maxIPs int, seen map[string]struct{}, rng *rand.Rand) ([]string, error) {
	lines, err := readLines(path)
	if err != nil {
		return nil, err
	}
	var nets []*net.IPNet
	for _, l := range lines {
		_, n, err := net.ParseCIDR(l)
		if err == nil && n.IP.To4() != nil {
			nets = append(nets, n)
		}
	}
	if len(nets) == 0 || maxIPs <= 0 {
		return nil, nil
	}
	rng.Shuffle(len(nets), func(i, j int) { nets[i], nets[j] = nets[j], nets[i] })

	perNet := maxIPs / len(nets)
	if perNet < 4 {
		perNet = 4
	}

	var ips []string
	for _, n := range nets {
		count := cidrCount(n)
		take := perNet
		if take > count {
			take = count
		}
		start := 0
		if count-take > 0 {
			start = rng.Intn(count - take)
		}
		base := binary.BigEndian.Uint32(n.IP.To4())
		for i := 0; i < take; i++ {
			ip := uint32ToIP(base + uint32(start+i))
			if _, dup := seen[ip]; dup {
				continue
			}
			seen[ip] = struct{}{}
			ips = append(ips, ip)
			if len(ips) >= maxIPs {
				break
			}
		}
		if len(ips) >= maxIPs {
			break
		}
	}
	rng.Shuffle(len(ips), func(i, j int) { ips[i], ips[j] = ips[j], ips[i] })
	return ips, nil
}

func cidrCount(n *net.IPNet) int {
	ones, bits := n.Mask.Size()
	if bits == 0 || bits-ones >= 31 {
		return 1 << 30 // cap pathologically large blocks
	}
	return 1 << (bits - ones)
}

func uint32ToIP(v uint32) string {
	var b [4]byte
	binary.BigEndian.PutUint32(b[:], v)
	return net.IP(b[:]).String()
}

func readLines(path string) ([]string, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer func() { _ = f.Close() }()

	var out []string
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 64*1024), 1<<20)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		out = append(out, line)
	}
	return out, sc.Err()
}
