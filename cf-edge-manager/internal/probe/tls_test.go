package probe

import "testing"

func TestParseCloudflareTraceIP(t *testing.T) {
	cases := []struct {
		name string
		body string
		want string
		ok   bool
	}{
		{"typical trace", "fl=1f2\nh=hcaptcha.com\nip=203.0.113.7\nts=1700000000\nsni=plaintext\n", "203.0.113.7", true},
		{"ip first line", "ip=198.51.100.9\ncolo=FRA\n", "198.51.100.9", true},
		{"no ip field", "fl=1f2\nh=hcaptcha.com\ncolo=FRA\n", "", false},
		{"ipv6 rejected", "ip=2606:4700::1111\n", "", false},
		{"garbage", "<html>403 Forbidden</html>", "", false},
		{"empty", "", "", false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got, err := parseCloudflareTraceIP(c.body)
			if c.ok && err != nil {
				t.Fatalf("want ip %q, got err %v", c.want, err)
			}
			if !c.ok && err == nil {
				t.Fatalf("want error, got ip %q", got)
			}
			if got != c.want {
				t.Fatalf("want %q, got %q", c.want, got)
			}
		})
	}
}
