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
