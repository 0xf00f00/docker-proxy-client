package scanner

import (
	"crypto/rand"
	"encoding/hex"
	"time"
)

// JobType distinguishes a full scan from a single-IP interactive test.
type JobType string

// Job types.
const (
	JobScan JobType = "scan"
	JobTest JobType = "test"
)

// JobState is the lifecycle state of a job.
type JobState string

// Job states.
const (
	StateQueued  JobState = "queued"
	StateRunning JobState = "running"
	StateDone    JobState = "done"
	StateFailed  JobState = "failed"
)

func (s JobState) terminal() bool { return s == StateDone || s == StateFailed }

// Job is a unit of work (scan or test) tracked through the store and surfaced
// to the dashboard.
type Job struct {
	ID         string     `json:"id"`
	Type       JobType    `json:"type"`
	IP         string     `json:"ip,omitempty"`
	State      JobState   `json:"state"`
	Error      string     `json:"error,omitempty"`
	CreatedAt  time.Time  `json:"created_at"`
	StartedAt  *time.Time `json:"started_at,omitempty"`
	FinishedAt *time.Time `json:"finished_at,omitempty"`
}

func newID() string {
	var b [6]byte
	_, _ = rand.Read(b[:])
	return hex.EncodeToString(b[:])
}
