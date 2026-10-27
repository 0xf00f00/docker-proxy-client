package dockerapi

import (
	"context"
	"encoding/binary"
	"net"
	"net/http"
	"path/filepath"
	"slices"
	"testing"
	"time"
)

func frame(stream byte, s string) []byte {
	b := make([]byte, 8, 8+len(s))
	b[0] = stream
	binary.BigEndian.PutUint32(b[4:], uint32(len(s)))
	return append(b, s...)
}

func TestClientCalls(t *testing.T) {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /containers/web/restart", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNoContent) })
	mux.HandleFunc("GET /containers/web/json", func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`{"State":{"StartedAt":"2026-10-27T01:43:11Z"}}`))
	})
	mux.HandleFunc("GET /containers/web/logs", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("since") != "1000" {
			http.Error(w, "bad query", http.StatusBadRequest)
			return
		}
		_, _ = w.Write(frame(2, "hello\n"))
	})
	sock := filepath.Join(t.TempDir(), "d.sock")
	l, err := net.Listen("unix", sock)
	if err != nil {
		t.Fatal(err)
	}
	srv := &http.Server{Handler: mux, ReadHeaderTimeout: time.Second}
	go func() { _ = srv.Serve(l) }()
	t.Cleanup(func() { _ = srv.Close() })
	c, ctx := New(sock), context.Background()

	if err := c.Restart(ctx, "web"); err != nil {
		t.Errorf("Restart: %v", err)
	}
	if got, err := c.StartedAt(ctx, "web"); err != nil || got.Year() != 2026 {
		t.Errorf("StartedAt = %v, %v", got, err)
	}
	if got, err := c.Logs(ctx, "web", time.Unix(1000, 0)); err != nil || !slices.Equal(got, []string{"hello"}) {
		t.Errorf("Logs = %q, %v", got, err)
	}
}
