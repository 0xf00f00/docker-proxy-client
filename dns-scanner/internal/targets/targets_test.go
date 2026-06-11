package targets

import (
	"net"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

func fixtureDir(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	files := map[string]string{
		"white_dns_iran.txt":   "1.1.1.1\n8.8.8.8\n# comment\n\n2.2.2.2\n",
		"ir-resolvers.txt":     "9.9.9.9\n1.1.1.1\n", // 1.1.1.1 dups the seed tier
		"ir-cidrs.txt":         "192.168.0.0/24\n10.0.0.0/30\nnot-a-cidr\n",
		"public_resolvers.txt": "208.67.222.222\n",
	}
	for name, body := range files {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return dir
}

// Same seed must produce the same ordering — the PLAN's resume guarantee.
func TestLoadIsDeterministic(t *testing.T) {
	dir := fixtureDir(t)
	a, err := Load(dir, 42, 8)
	if err != nil {
		t.Fatal(err)
	}
	b, err := Load(dir, 42, 8)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(a, b) {
		t.Errorf("same seed gave different tiers:\n%v\n%v", a, b)
	}
}

func TestLoadDedupsAndValidates(t *testing.T) {
	tiers, err := Load(fixtureDir(t), 1, 100)
	if err != nil {
		t.Fatal(err)
	}
	seen := map[string]bool{}
	for _, tier := range tiers {
		for _, ip := range tier.IPs {
			if net.ParseIP(ip) == nil {
				t.Errorf("tier %q yielded invalid IP %q", tier.Name, ip)
			}
			if seen[ip] {
				t.Errorf("duplicate IP across tiers: %q", ip)
			}
			seen[ip] = true
		}
	}
	if seen["1.1.1.1"] != true {
		t.Error("expected 1.1.1.1 present once")
	}
}

func TestLoadCIDRSampleBounded(t *testing.T) {
	const max = 8
	tiers, err := Load(fixtureDir(t), 7, max)
	if err != nil {
		t.Fatal(err)
	}
	for _, tier := range tiers {
		if tier.Name == "ir-cidr" && len(tier.IPs) > max {
			t.Errorf("ir-cidr tier = %d IPs, want <= %d", len(tier.IPs), max)
		}
	}
}

func TestCIDRCount(t *testing.T) {
	cases := map[string]int{
		"10.0.0.0/30": 4,
		"10.0.0.0/24": 256,
		"10.0.0.0/32": 1,
		"10.0.0.0/31": 2,
		"0.0.0.0/0":   1 << 30, // pathologically large → capped
	}
	for cidr, want := range cases {
		_, n, err := net.ParseCIDR(cidr)
		if err != nil {
			t.Fatal(err)
		}
		if got := cidrCount(n); got != want {
			t.Errorf("cidrCount(%s) = %d, want %d", cidr, got, want)
		}
	}
}
