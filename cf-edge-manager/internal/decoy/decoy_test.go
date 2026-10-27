package decoy

import (
	"testing"
)

func TestValidRejectsInjection(t *testing.T) {
	for _, bad := range []string{"", "a", "A.com", "a b.com", "a.com\nconnect=1.1.1.1", "a.com\n", "-a.com", "a..com", "a.com;rm", "$1.com"} {
		if Valid(bad) {
			t.Errorf("Valid(%q) = true", bad)
		}
	}
	if !Valid("www.example.com") {
		t.Error("Valid rejected a plain hostname")
	}
}
