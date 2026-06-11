package score

import (
	"testing"
	"time"
)

func TestRankWorkingLeastLossThenSpeed(t *testing.T) {
	in := []Result{
		{IP: "slow-lowloss", LossFrac: 0.0, DownloadMTU: 512, CertRTTms: 500},
		{IP: "fast-lowloss", LossFrac: 0.0, DownloadMTU: 1232, CertRTTms: 50},
		{IP: "lossy", LossFrac: 0.5, DownloadMTU: 1232, CertRTTms: 10},
	}
	got := RankWorking(in)
	want := []string{"fast-lowloss", "slow-lowloss", "lossy"}
	for i, w := range want {
		if got[i].IP != w {
			t.Errorf("rank[%d] = %q, want %q (full: %v)", i, got[i].IP, w, ips(got))
		}
	}
}

func TestRankWorkingDoesNotMutateInput(t *testing.T) {
	in := []Result{{IP: "a", LossFrac: 0.5}, {IP: "b", LossFrac: 0.1}}
	_ = RankWorking(in)
	if in[0].IP != "a" {
		t.Errorf("input reordered: %v", ips(in))
	}
}

func TestSpeedScoreFallbacks(t *testing.T) {
	// No download MTU and no EDNS → floor of 512; no cert RTT → uses aliveRTT.
	r := Result{EDNSMax: 0, DownloadMTU: 0, AliveRTT: 50 * time.Millisecond}
	if got := r.SpeedScore(); got != int64(512)*1000/(50+50) {
		t.Errorf("SpeedScore fallback = %d", got)
	}
	// Higher MTU at equal latency scores higher.
	hi := Result{DownloadMTU: 1232, CertRTTms: 50}
	lo := Result{DownloadMTU: 512, CertRTTms: 50}
	if hi.SpeedScore() <= lo.SpeedScore() {
		t.Errorf("higher MTU did not score higher: %d <= %d", hi.SpeedScore(), lo.SpeedScore())
	}
}

func ips(rs []Result) []string {
	out := make([]string, len(rs))
	for i, r := range rs {
		out[i] = r.IP
	}
	return out
}
