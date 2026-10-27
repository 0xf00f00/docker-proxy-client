package probe

import (
	"net"
	"testing"
	"time"
)

func TestTCPLoss(t *testing.T) {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	port := l.Addr().(*net.TCPAddr).Port
	go func() {
		for {
			c, err := l.Accept()
			if err != nil {
				return
			}
			_ = c.Close()
		}
	}()
	if got := TCPLoss("127.0.0.1", port, 30, time.Second); got != 0 {
		t.Errorf("listening port: loss = %v, want 0", got)
	}
	_ = l.Close()
	if got := TCPLoss("127.0.0.1", port, 30, time.Second); got != 1 {
		t.Errorf("closed port: loss = %v, want 1", got)
	}
}
