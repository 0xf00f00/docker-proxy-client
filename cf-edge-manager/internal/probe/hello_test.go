package probe

import (
	"context"
	"net"
	"net/http"
	"net/http/httptest"
	"strconv"
	"testing"
	"time"
)

func TestTLSHello(t *testing.T) {
	srv := httptest.NewTLSServer(http.NotFoundHandler())
	defer srv.Close()
	host, portStr, _ := net.SplitHostPort(srv.Listener.Addr().String())
	port, _ := strconv.Atoi(portStr)

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	for _, fp := range []string{"", "firefox"} {
		if !TLSHello(ctx, host, port, "a.example", fp) {
			t.Errorf("utls=%q: handshake to a live TLS server must pass", fp)
		}
	}

	l, _ := net.Listen("tcp", "127.0.0.1:0")
	closed := l.Addr().(*net.TCPAddr).Port
	_ = l.Close()
	if TLSHello(ctx, "127.0.0.1", closed, "a.example", "") {
		t.Error("closed port must fail")
	}
}
