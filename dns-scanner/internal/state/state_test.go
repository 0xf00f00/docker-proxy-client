package state

import (
	"path/filepath"
	"reflect"
	"testing"
	"time"
)

func TestSaveLoadRoundTrip(t *testing.T) {
	p := filepath.Join(t.TempDir(), "state.json")
	want := State{
		UpdatedUnix: 1700000000,
		BackoffDays: 3,
		Working:     []Working{{IP: "1.1.1.1", UploadMTU: 200, DownloadMTU: 900, EDNSMax: 1232, LossPct: 0}},
		History:     []Historic{{IP: "8.8.8.8", LastWorkingUnix: 1699999999, UploadMTU: 180}},
	}
	if err := Save(p, want); err != nil {
		t.Fatal(err)
	}
	got, err := Load(p)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("round trip mismatch:\n got %+v\nwant %+v", got, want)
	}
}

func TestLoadMissingFileIsEmpty(t *testing.T) {
	got, err := Load(filepath.Join(t.TempDir(), "absent.json"))
	if err != nil {
		t.Fatalf("Load(missing) err = %v, want nil", err)
	}
	if !reflect.DeepEqual(got, State{}) {
		t.Errorf("Load(missing) = %+v, want zero State", got)
	}
}

func TestPruneCertFailedDropsExpiredAndKeepsFresh(t *testing.T) {
	const now = 1_000_000
	ttl := 10 * 24 * time.Hour
	ttlSec := int64(ttl / time.Second)
	in := map[string]int64{
		"fresh":   now - 1,          // within TTL → kept
		"edge":    now - ttlSec + 1, // just within TTL → kept
		"expired": now - ttlSec,     // exactly TTL old → dropped (now-ts < ttl is false)
		"ancient": now - 2*ttlSec,   // dropped
	}
	got := PruneCertFailed(in, now, ttl, 100)
	want := map[string]int64{"fresh": now - 1, "edge": now - ttlSec + 1}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("PruneCertFailed = %v, want %v", got, want)
	}
}

func TestPruneCertFailedEvictsOldestOverCap(t *testing.T) {
	const now = 1_000_000
	in := map[string]int64{"a": now - 30, "b": now - 20, "c": now - 10}
	got := PruneCertFailed(in, now, time.Hour, 2) // cap 2 → drop oldest "a"
	want := map[string]int64{"b": now - 20, "c": now - 10}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("PruneCertFailed(cap=2) = %v, want %v", got, want)
	}
}

func TestPruneCertFailedEmptyIsNil(t *testing.T) {
	if got := PruneCertFailed(nil, 1, time.Hour, 10); got != nil {
		t.Errorf("PruneCertFailed(nil) = %v, want nil", got)
	}
	allExpired := map[string]int64{"x": 0}
	if got := PruneCertFailed(allExpired, 1_000_000, time.Hour, 10); got != nil {
		t.Errorf("PruneCertFailed(all expired) = %v, want nil (keeps state JSON clean)", got)
	}
}

func TestRecentHistoryIPsOrdersExcludesAndLimits(t *testing.T) {
	s := State{History: []Historic{
		{IP: "old", LastWorkingUnix: 100},
		{IP: "newest", LastWorkingUnix: 300},
		{IP: "mid", LastWorkingUnix: 200},
		{IP: "skip", LastWorkingUnix: 400},
	}}
	got := s.RecentHistoryIPs(2, map[string]struct{}{"skip": {}})
	want := []string{"newest", "mid"} // most-recent first, "skip" excluded, capped at 2
	if !reflect.DeepEqual(got, want) {
		t.Errorf("RecentHistoryIPs = %v, want %v", got, want)
	}
}
