package assign

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func writeFile(t *testing.T, dir, body string) string {
	t.Helper()
	p := filepath.Join(dir, "client_resolvers.txt")
	if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestWriteManagedPreservesHumanEntries(t *testing.T) {
	dir := t.TempDir()
	p := writeFile(t, dir, "# my own resolver\n9.9.9.9\n")

	changed, err := WriteManaged(p, []string{"1.1.1.1", "8.8.8.8"})
	if err != nil || !changed {
		t.Fatalf("WriteManaged = (%v, %v), want (true, nil)", changed, err)
	}

	data, _ := os.ReadFile(p)
	body := string(data)
	if !strings.Contains(body, "9.9.9.9") || !strings.Contains(body, "# my own resolver") {
		t.Errorf("human entries lost:\n%s", body)
	}

	got := ReadManaged(p)
	if len(got) != 2 || got[0] != "1.1.1.1" || got[1] != "8.8.8.8" {
		t.Errorf("ReadManaged = %v, want [1.1.1.1 8.8.8.8]", got)
	}
}

func TestWriteManagedNoChangeIsIdempotent(t *testing.T) {
	dir := t.TempDir()
	p := writeFile(t, dir, "")
	if _, err := WriteManaged(p, []string{"1.1.1.1", "2.2.2.2"}); err != nil {
		t.Fatal(err)
	}
	// Same set, different order → unchanged.
	changed, err := WriteManaged(p, []string{"2.2.2.2", "1.1.1.1"})
	if err != nil {
		t.Fatal(err)
	}
	if changed {
		t.Error("WriteManaged reported a change for the same set")
	}
}

// An empty set must never clobber the last-known-good managed block.
func TestWriteManagedEmptyIsNoop(t *testing.T) {
	dir := t.TempDir()
	p := writeFile(t, dir, "")
	_, _ = WriteManaged(p, []string{"1.1.1.1"})

	changed, err := WriteManaged(p, nil)
	if err != nil || changed {
		t.Fatalf("WriteManaged(nil) = (%v, %v), want (false, nil)", changed, err)
	}
	if got := ReadManaged(p); len(got) != 1 || got[0] != "1.1.1.1" {
		t.Errorf("managed block clobbered: %v", got)
	}
}

// A resolver the user added by hand must not be re-homed into the managed block
// (which would list it twice) even when the scanner also verifies it working.
func TestWriteManagedDoesNotDuplicateHumanResolver(t *testing.T) {
	dir := t.TempDir()
	p := writeFile(t, dir, "9.9.9.9\n")

	if _, err := WriteManaged(p, []string{"9.9.9.9", "1.1.1.1"}); err != nil {
		t.Fatal(err)
	}

	data, _ := os.ReadFile(p)
	if n := strings.Count(string(data), "9.9.9.9"); n != 1 {
		t.Errorf("9.9.9.9 listed %d times, want 1:\n%s", n, data)
	}
	if got := ReadManaged(p); len(got) != 1 || got[0] != "1.1.1.1" {
		t.Errorf("ReadManaged = %v, want [1.1.1.1] (human resolver stays a human line)", got)
	}
	// But the full set mdns uses still contains both.
	if got := ReadAll(p); strings.Join(got, ",") != "9.9.9.9,1.1.1.1" {
		t.Errorf("ReadAll = %v, want both resolvers", got)
	}
}
