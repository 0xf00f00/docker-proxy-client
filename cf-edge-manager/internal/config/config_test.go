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

func TestLoadOnlyTakesTheOperatorsSettings(t *testing.T) {
	t.Setenv("SCAN_ENABLE", "true")
	t.Setenv("SCAN_CRON", "0 0 5 * * 1")
	t.Setenv("SELECT_ENABLE", "true")
	t.Setenv("PROBE_UUID", "id")
	t.Setenv("PROBE_ORIGIN_HOST", "origin.example")
	t.Setenv("SNISPOOF_INSTANCES", "solo")
	// fixed behavior must not be steerable from the environment
	t.Setenv("KEEP_LOSS_MAX", "0.99")
	t.Setenv("POOL_SIZE", "1")
	c, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if !c.ScanEnable || c.ScanCron != "0 0 5 * * 1" || !c.SelectEnable || c.ProbeUUID != "id" || len(c.Instances) != 1 || c.ProbePath != "/" {
		t.Errorf("operator settings not applied: %+v", c)
	}
	if d := Defaults(); c.KeepMax != d.KeepMax || c.PoolSize != d.PoolSize {
		t.Errorf("keep=%v pool=%d: fixed behavior leaked from the environment", c.KeepMax, c.PoolSize)
	}
}

func TestSelectNeedsTheWireIdentity(t *testing.T) {
	t.Setenv("SELECT_ENABLE", "true")
	if _, err := Load(); err == nil {
		t.Error("selection without PROBE_UUID would read every edge as failed: refuse to start")
	}
}
