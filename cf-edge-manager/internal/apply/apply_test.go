package apply

import (
	"os"
	"path/filepath"
	"testing"
)

func writeIni(t *testing.T, body string) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), "c.ini")
	if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestFakeSNIReadWrite(t *testing.T) {
	p := writeIni(t, "listen = 0.0.0.0:40443\nconnect = 1.2.3.4:443\nfake-sni = hcaptcha.com\nutls = firefox\n")
	a := &Applier{SnispoofConf: p}
	if got := a.CurrentFakeSNI(); got != "hcaptcha.com" {
		t.Fatalf("current = %q", got)
	}
	if err := a.WriteFakeSNI("chatgpt.com"); err != nil {
		t.Fatal(err)
	}
	b, _ := os.ReadFile(p)
	if want := "listen = 0.0.0.0:40443\nconnect = 1.2.3.4:443\nfake-sni = chatgpt.com\nutls = firefox\n"; string(b) != want {
		t.Errorf("file = %q want %q", b, want)
	}
	if a.CurrentFakeSNI() != "chatgpt.com" {
		t.Error("read-back mismatch")
	}
}

// \s spans newlines: an empty value must not pull in (or overwrite) the next line.
func TestFakeSNIEmptyValueStaysOnItsLine(t *testing.T) {
	p := writeIni(t, "fake-sni =\nutls = firefox\n")
	a := &Applier{SnispoofConf: p}
	if got := a.CurrentFakeSNI(); got != "" {
		t.Fatalf("current = %q, want empty", got)
	}
	if err := a.WriteFakeSNI("chatgpt.com"); err != nil {
		t.Fatal(err)
	}
	if b, _ := os.ReadFile(p); string(b) != "fake-sni =chatgpt.com\nutls = firefox\n" {
		t.Errorf("file = %q", b)
	}
}

func TestWriteFakeSNIRejectsInvalid(t *testing.T) {
	const body = "fake-sni = a.example\n"
	p := writeIni(t, body)
	for _, bad := range []string{"", "a.example\nconnect = 1.2.3.4:443", "$1.example", "A.example"} {
		if err := (&Applier{SnispoofConf: p}).WriteFakeSNI(bad); err == nil {
			t.Errorf("WriteFakeSNI(%q) accepted", bad)
		}
	}
	if b, _ := os.ReadFile(p); string(b) != body {
		t.Errorf("rejected writes must leave the file alone: %q", b)
	}
}

func TestConnectEmptyValueStaysOnItsLine(t *testing.T) {
	p := writeIni(t, "connect =\nfake-sni = a.example\n")
	a := &Applier{SnispoofConf: p, Port: 443}
	if got := a.CurrentSnispoof(); got != "" {
		t.Fatalf("current = %q, want empty", got)
	}
	if err := a.WriteSnispoof("192.0.2.7"); err != nil {
		t.Fatal(err)
	}
	if b, _ := os.ReadFile(p); string(b) != "connect =192.0.2.7:443\nfake-sni = a.example\n" {
		t.Errorf("file = %q", b)
	}
}

func TestWriteEdgeRejectsNonIP(t *testing.T) {
	const body = "connect = 192.0.2.1:443\n"
	p := writeIni(t, body)
	a := &Applier{SnispoofConf: p, Port: 443, FrontHost: "front.example", CorednsHosts: filepath.Join(t.TempDir(), "hosts")}
	for _, bad := range []string{"", "192.0.2.1\nfake-sni = x.example", "$1", "front.example"} {
		if a.WriteSnispoof(bad) == nil || a.WriteByedpi(bad) == nil {
			t.Errorf("accepted edge %q", bad)
		}
	}
	if b, _ := os.ReadFile(p); string(b) != body {
		t.Errorf("rejected writes must leave the ini alone: %q", b)
	}
	if err := (&Applier{SnispoofConf: writeIni(t, "fake-sni = a.example\n"), Port: 443}).WriteSnispoof("192.0.2.1"); err == nil {
		t.Error("expected error when the ini has no connect line")
	}
}
