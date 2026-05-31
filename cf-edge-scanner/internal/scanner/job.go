package scanner

import (
	"crypto/rand"
	"encoding/hex"
	"time"
)

type JobType string

const (
	JobScan JobType = "scan"
	JobTest JobType = "test"
)

type JobState string

const (
	StateQueued  JobState = "queued"
	StateRunning JobState = "running"
	StateDone    JobState = "done"
	StateFailed  JobState = "failed"
)

func (s JobState) terminal() bool { return s == StateDone || s == StateFailed }

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
