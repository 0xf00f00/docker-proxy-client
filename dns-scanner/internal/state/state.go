// Package state persists the working set, the resolver history pool, and the
// backoff level across restarts so a daemon resumes instead of starting cold.
package state

import (
	"encoding/json"
	"os"
	"sort"
	"time"
)

// Working is a resolver currently in the active set.
type Working struct {
	IP          string `json:"ip"`
	UploadMTU   int    `json:"up_mtu,omitempty"`
	DownloadMTU int    `json:"down_mtu,omitempty"`
	EDNSMax     int    `json:"edns_max,omitempty"`
	LossPct     int    `json:"loss_pct,omitempty"`
}

// Historic is a resolver that certified at some point. We keep these and retry
// them first in future scans — in a severe-disruption network a resolver that
// worked once is a far better bet than a random IP when it briefly reappears.
type Historic struct {
	IP              string `json:"ip"`
	LastWorkingUnix int64  `json:"last_working_unix"`
	UploadMTU       int    `json:"up_mtu,omitempty"`
	DownloadMTU     int    `json:"down_mtu,omitempty"`
}

type State struct {
	UpdatedUnix int64      `json:"updated_unix"`
	BackoffDays int        `json:"backoff_days"`
	Working     []Working  `json:"working"`
	History     []Historic `json:"history"`

	// CertFailed maps IP -> last cert-failure unix; sweep candidates here are
	// skipped until the TTL expires. A cert failure is the faithful verdict (a
	// gate flake is not), so it's safe to remember.
	CertFailed map[string]int64 `json:"cert_failed,omitempty"`
}

// Load returns the persisted state, or an empty state if the file is absent.
func Load(path string) (State, error) {
	var s State
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return s, nil
		}
		return s, err
	}
	if err := json.Unmarshal(data, &s); err != nil {
		return State{}, err
	}
	return s, nil
}

// Save writes state atomically (temp + rename) so a crash mid-write can't tear it.
func Save(path string, s State) error {
	data, err := json.MarshalIndent(s, "", "  ")
	if err != nil {
		return err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

func (s State) IPs() []string {
	out := make([]string, len(s.Working))
	for i, w := range s.Working {
		out[i] = w.IP
	}
	return out
}

// PruneCertFailed drops cert-failure entries older than ttl and, if still over
// max, evicts the oldest until at most max remain. Returns nil for an empty
// result so the field stays omitted from state JSON.
func PruneCertFailed(m map[string]int64, now int64, ttl time.Duration, max int) map[string]int64 {
	if len(m) == 0 {
		return nil
	}
	ttlSec := int64(ttl / time.Second)
	out := make(map[string]int64, len(m))
	for ip, ts := range m {
		if now-ts < ttlSec {
			out[ip] = ts
		}
	}
	if max > 0 && len(out) > max {
		type kv struct {
			ip string
			ts int64
		}
		kvs := make([]kv, 0, len(out))
		for ip, ts := range out {
			kvs = append(kvs, kv{ip, ts})
		}
		sort.Slice(kvs, func(i, j int) bool { return kvs[i].ts > kvs[j].ts }) // newest first
		out = make(map[string]int64, max)
		for _, e := range kvs[:max] {
			out[e.ip] = e.ts
		}
	}
	if len(out) == 0 {
		return nil
	}
	return out
}

// RecentHistoryIPs returns up to k most-recently-working history IPs, skipping
// any already in exclude.
func (s State) RecentHistoryIPs(k int, exclude map[string]struct{}) []string {
	h := append([]Historic(nil), s.History...)
	sort.SliceStable(h, func(i, j int) bool { return h[i].LastWorkingUnix > h[j].LastWorkingUnix })
	var out []string
	for _, e := range h {
		if len(out) >= k {
			break
		}
		if _, skip := exclude[e.IP]; skip {
			continue
		}
		out = append(out, e.IP)
	}
	return out
}
