// Package dockerapi is the few Docker Engine calls the manager needs, over the unix socket.
package dockerapi

import (
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strconv"
	"time"
)

const maxLogBytes = 8 << 20

// Client talks to the Docker Engine API.
type Client struct{ http *http.Client }

// New returns a client for the engine on the unix socket sock.
func New(sock string) *Client {
	tr := &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
		return (&net.Dialer{}).DialContext(ctx, "unix", sock)
	}}
	return &Client{http: &http.Client{Transport: tr, Timeout: 30 * time.Second}}
}

func (c *Client) do(ctx context.Context, method, path string, query url.Values) (*http.Response, error) {
	u := "http://docker" + path
	if len(query) > 0 {
		u += "?" + query.Encode()
	}
	req, err := http.NewRequestWithContext(ctx, method, u, nil)
	if err != nil {
		return nil, err
	}
	return c.http.Do(req)
}

// Restart restarts the named container.
func (c *Client) Restart(ctx context.Context, name string) error {
	resp, err := c.do(ctx, http.MethodPost, "/containers/"+url.PathEscape(name)+"/restart", url.Values{"t": {"10"}})
	if err != nil {
		return err
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusNoContent {
		return fmt.Errorf("docker restart %s: status %d", name, resp.StatusCode)
	}
	return nil
}

// StartedAt is when the named container last started.
func (c *Client) StartedAt(ctx context.Context, name string) (time.Time, error) {
	resp, err := c.do(ctx, http.MethodGet, "/containers/"+url.PathEscape(name)+"/json", nil)
	if err != nil {
		return time.Time{}, err
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		return time.Time{}, fmt.Errorf("docker inspect %s: status %d", name, resp.StatusCode)
	}
	var v struct{ State struct{ StartedAt time.Time } }
	if err := json.NewDecoder(resp.Body).Decode(&v); err != nil {
		return time.Time{}, err
	}
	return v.State.StartedAt, nil
}

// Logs returns the container's log lines since the given time. The container must not
// use a TTY, or the stream is not framed.
func (c *Client) Logs(ctx context.Context, name string, since time.Time) ([]string, error) {
	q := url.Values{"stdout": {"1"}, "stderr": {"1"}, "since": {strconv.FormatInt(since.Unix(), 10)}}
	resp, err := c.do(ctx, http.MethodGet, "/containers/"+url.PathEscape(name)+"/logs", q)
	if err != nil {
		return nil, err
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("docker logs %s: status %d", name, resp.StatusCode)
	}
	return Lines(io.LimitReader(resp.Body, maxLogBytes)), nil
}

// Lines splits a multiplexed docker log stream (8-byte frame headers) into lines.
func Lines(r io.Reader) []string {
	var lines []string
	hdr := make([]byte, 8)
	for {
		if _, err := io.ReadFull(r, hdr); err != nil {
			return lines
		}
		frame := make([]byte, binary.BigEndian.Uint32(hdr[4:]))
		if _, err := io.ReadFull(r, frame); err != nil {
			return lines
		}
		for _, ln := range bytes.Split(frame, []byte{'\n'}) {
			if len(ln) > 0 {
				lines = append(lines, string(ln))
			}
		}
	}
}
