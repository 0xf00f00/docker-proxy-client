package config

import (
	"slices"
	"testing"
)

func TestParseInstances(t *testing.T) {
	got, err := parseInstances("sni-spoofing, sni-spoofing-fallback\nthird", "/instances", "/state")
	if err != nil {
		t.Fatal(err)
	}
	want := []Instance{
		{"sni-spoofing", "/instances/sni-spoofing/config.ini", "/state/picker-sni-spoofing.json"},
		{"sni-spoofing-fallback", "/instances/sni-spoofing-fallback/config.ini", "/state/picker-sni-spoofing-fallback.json"},
		{"third", "/instances/third/config.ini", "/state/picker-third.json"},
	}
	if !slices.Equal(got, want) {
		t.Errorf("got %v, want %v (order is priority)", got, want)
	}
}

func TestParseInstancesRejects(t *testing.T) {
	for _, list := range []string{"", " , ", "a/b", "..", "-a", "a b/../c", "a,a"} {
		if got, err := parseInstances(list, "/i", "/s"); err == nil {
			t.Errorf("%q: want an error, got %v", list, got)
		}
	}
}

func TestPrimaryConf(t *testing.T) {
	c := Config{Instances: []Instance{{Name: "a", Conf: "/i/a/config.ini"}, {Name: "b", Conf: "/i/b/config.ini"}}}
	if got := c.PrimaryConf(); got != "/i/a/config.ini" {
		t.Errorf("PrimaryConf = %q, want the first instance", got)
	}
	if (Config{}).PrimaryConf() != "" {
		t.Error("no instances must give an empty path")
	}
}
