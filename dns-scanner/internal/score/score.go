// Package score holds the per-resolver result and the ranking used to pick the
// best working set: certified first, then least loss, then speed.
package score

import (
	"sort"
	"time"
)

// Result captures a resolver's funnel outcome and (if it got that far) the
// engine certification verdict.
type Result struct {
	IP string

	AliveRTT time.Duration
	NXOK     bool
	Forwards bool
	EDNSMax  int
	UploadOK bool
	LossFrac float64
	Jitter   time.Duration

	Certified     bool
	UploadMTU     int
	DownloadMTU   int
	CertRTTms     int64
	CertifyReason string
}

// GatesPassed reports whether the cheap funnel cleared the resolver for the
// expensive engine certification.
func (r Result) GatesPassed() bool {
	return r.AliveRTT > 0 && r.NXOK && r.Forwards && r.UploadOK && r.EDNSMax > 0
}

// SpeedScore = effectiveMTU·1000 / (latency+50). Higher is better.
func (r Result) SpeedScore() int64 {
	mtu := r.DownloadMTU
	if mtu == 0 {
		if mtu = r.EDNSMax; mtu == 0 {
			mtu = 512
		}
	}
	lat := r.CertRTTms
	if lat <= 0 {
		lat = r.AliveRTT.Milliseconds()
	}
	return int64(mtu) * 1000 / (lat + 50)
}

// RankWorking orders certified resolvers: least loss first, then highest speed.
func RankWorking(rs []Result) []Result {
	out := append([]Result(nil), rs...)
	sort.SliceStable(out, func(i, j int) bool {
		if out[i].LossFrac != out[j].LossFrac {
			return out[i].LossFrac < out[j].LossFrac
		}
		return out[i].SpeedScore() > out[j].SpeedScore()
	})
	return out
}
