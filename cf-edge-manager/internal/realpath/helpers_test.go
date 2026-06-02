package realpath

import "testing"

func TestSplitURL(t *testing.T) {
	cases := []struct{ in, host, path string }{
		{"http://www.gstatic.com/generate_204", "www.gstatic.com:80", "/generate_204"},
		{"https://example.com:8443/x/y", "example.com:8443", "/x/y"},
		{"http://host", "host:80", "/"},
	}
	for _, c := range cases {
		h, p := splitURL(c.in)
		if h != c.host || p != c.path {
			t.Errorf("splitURL(%q) = (%q,%q), want (%q,%q)", c.in, h, p, c.host, c.path)
		}
	}
}

func TestHostOnly(t *testing.T) {
	if got := hostOnly("www.gstatic.com:80"); got != "www.gstatic.com" {
		t.Errorf("hostOnly = %q", got)
	}
	if got := hostOnly("nohost"); got != "nohost" {
		t.Errorf("hostOnly(nohost) = %q", got)
	}
}
