package decoy

import (
	"context"
	"reflect"
	"strings"
	"testing"
)

// Every non-comment line of seeds.txt must survive Seeds(); a typo or duplicate
// would otherwise be dropped silently.
func TestSeedsFileClean(t *testing.T) {
	lines := 0
	for _, ln := range strings.Split(seedsFile, "\n") {
		ln, _, _ = strings.Cut(ln, "#")
		if strings.TrimSpace(ln) != "" {
			lines++
		}
	}
	s := Seeds()
	if len(s) != lines || len(s) < 5 {
		t.Fatalf("seeds.txt has %d names but Seeds() kept %d (invalid or duplicate lines?)", lines, len(s))
	}
	if s[0] != Default {
		t.Errorf("first seed = %q, want Default %q", s[0], Default)
	}
}

func TestValidRejectsInjection(t *testing.T) {
	for _, bad := range []string{"", "a", "A.com", "a b.com", "a.com\nconnect=1.1.1.1", "a.com\n", "-a.com", "a..com", "a.com;rm", "$1.com"} {
		if Valid(bad) {
			t.Errorf("Valid(%q) = true", bad)
		}
	}
	if !Valid("www.example.com") {
		t.Error("Valid rejected a plain hostname")
	}
}

func TestEdges(t *testing.T) {
	if got := Edges("b", []string{"a", "b", "c", "d"}, 3); !reflect.DeepEqual(got, []string{"b", "a", "c"}) {
		t.Errorf("Edges = %v", got)
	}
	if got := Edges("", []string{"a"}, 3); !reflect.DeepEqual(got, []string{"a"}) {
		t.Errorf("Edges with no in-use = %v", got)
	}
}

func TestScreenAndRank(t *testing.T) {
	pass := map[string]map[string]bool{
		"e1": {"a.com": true, "b.com": true, "c.com": false},
		"e2": {"a.com": true, "b.com": false, "c.com": false},
	}
	check := func(_ context.Context, edge, sni string) bool { return pass[edge][sni] }
	sc := Screen(context.Background(), []string{"e1", "e2"}, []string{"c.com", "b.com", "a.com"}, check)
	want := []Score{{"c.com", 0, 2}, {"b.com", 1, 2}, {"a.com", 2, 2}}
	if !reflect.DeepEqual(sc, want) {
		t.Fatalf("scores %v want %v", sc, want)
	}
	if got := Rank(sc); !reflect.DeepEqual(got, []string{"a.com", "b.com"}) {
		t.Errorf("rank = %v", got)
	}
}

func TestScreenCancelled(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	sc := Screen(ctx, []string{"e"}, []string{"a.com"}, func(context.Context, string, string) bool { return true })
	if sc[0].Pass != 0 {
		t.Errorf("cancelled screen must not pass: %v", sc)
	}
}
