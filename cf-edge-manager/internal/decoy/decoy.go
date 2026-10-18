// Package decoy screens candidate sni-spoofing decoy SNIs (the name DPI sees in
// the fake ClientHello).
package decoy

import (
	"context"
	_ "embed" // seeds.txt
	"regexp"
	"slices"
	"strings"
	"sync"
)

//go:embed seeds.txt
var seedsFile string

var validName = regexp.MustCompile(`^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$`)

const screenConc = 4

// Default is the decoy when the sni-spoofing config names none; seeds.txt starts with it.
const Default = "chatgpt.com"

// Valid reports whether s is a plain lowercase hostname.
func Valid(s string) bool { return len(s) <= 253 && validName.MatchString(s) }

// Seeds returns the embedded candidates in preference order.
func Seeds() []string {
	var out []string
	for _, ln := range strings.Split(seedsFile, "\n") {
		ln, _, _ = strings.Cut(ln, "#")
		if n := strings.TrimSpace(ln); Valid(n) && !slices.Contains(out, n) {
			out = append(out, n)
		}
	}
	return out
}

// Edges is inUse followed by pool, de-duplicated, at most n.
func Edges(inUse string, pool []string, n int) []string {
	var out []string
	for _, ip := range append([]string{inUse}, pool...) {
		if len(out) == n {
			break
		}
		if ip != "" && !slices.Contains(out, ip) {
			out = append(out, ip)
		}
	}
	return out
}

// Check reports whether a TLS hello naming sni completes against edge.
type Check func(ctx context.Context, edge, sni string) bool

// Score is one candidate's result across the screened edges.
type Score struct {
	SNI   string `json:"sni"`
	Pass  int    `json:"pass"`
	Tried int    `json:"tried"`
}

// Screen runs check over every (edge, candidate) pair, a few at a time.
func Screen(ctx context.Context, edges, cands []string, check Check) []Score {
	scores := make([]Score, len(cands))
	for i, c := range cands {
		scores[i] = Score{SNI: c, Tried: len(edges)}
	}
	var (
		mu  sync.Mutex
		wg  sync.WaitGroup
		sem = make(chan struct{}, screenConc)
	)
	for i, c := range cands {
		for _, e := range edges {
			wg.Add(1)
			sem <- struct{}{}
			go func() {
				defer wg.Done()
				defer func() { <-sem }()
				if ctx.Err() == nil && check(ctx, e, c) {
					mu.Lock()
					scores[i].Pass++
					mu.Unlock()
				}
			}()
		}
	}
	wg.Wait()
	return scores
}

// Rank returns the passing names, full passes first, otherwise in input order.
func Rank(scores []Score) []string {
	var full, part []string
	for _, s := range scores {
		if s.Pass == s.Tried && s.Pass > 0 {
			full = append(full, s.SNI)
		} else if s.Pass > 0 {
			part = append(part, s.SNI)
		}
	}
	return append(full, part...)
}
