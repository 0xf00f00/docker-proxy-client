package manager

import (
	"context"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"slices"
	"testing"

	"github.com/0xf00f00/cf-edge-manager/internal/config"
)

// newTestManager builds one instance per name, on edges 192.0.2.1, .2, ... and
// decoys <name>.example; byedpi (owned by the last) sits on 198.51.100.9.
func newTestManager(t *testing.T, names ...string) *Manager {
	t.Helper()
	cfg := config.Config{Port: 443, OutDir: t.TempDir(), CorednsHosts: filepath.Join(t.TempDir(), "edge.hosts"), FrontHost: "front.example"}
	if err := os.WriteFile(cfg.CorednsHosts, []byte("198.51.100.9 front.example\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for i, name := range names {
		conf := filepath.Join(t.TempDir(), "config.ini")
		ini := "connect = 192.0.2." + string(rune('1'+i)) + ":443\nfake-sni = " + name + ".example\n"
		if err := os.WriteFile(conf, []byte(ini), 0o600); err != nil {
			t.Fatal(err)
		}
		cfg.Instances = append(cfg.Instances, config.Instance{Name: name, Conf: conf, State: filepath.Join(t.TempDir(), name+".json")})
	}
	return New(cfg, slog.New(slog.NewTextHandler(io.Discard, nil)))
}

func TestStatusListsInstancesInPriorityOrder(t *testing.T) {
	st := newTestManager(t, "main", "standby").Status()
	insts := st["instances"].([]map[string]string)
	if len(insts) != 2 || insts[0]["name"] != "main" || insts[0]["edge"] != "192.0.2.1" || insts[0]["fake_sni"] != "main.example" ||
		insts[1]["name"] != "standby" || insts[1]["edge"] != "192.0.2.2" {
		t.Errorf("instances: %v", insts)
	}
	if st["byedpi"] != "198.51.100.9" {
		t.Errorf("byedpi = %v, want the last instance's path", st["byedpi"])
	}
}

func TestPeersAreEveryoneElse(t *testing.T) {
	m := newTestManager(t, "a", "b", "c")
	edges, decoys := m.peersOf(0)()
	if !slices.Equal(edges, []string{"192.0.2.2", "192.0.2.3", "198.51.100.9"}) || !slices.Equal(decoys, []string{"b.example", "c.example"}) {
		t.Errorf("first instance must avoid the others and the byedpi edge: %v %v", edges, decoys)
	}
	edges, decoys = m.peersOf(2)()
	if !slices.Equal(edges, []string{"192.0.2.1", "192.0.2.2"}) || !slices.Equal(decoys, []string{"a.example", "b.example"}) {
		t.Errorf("the byedpi owner already knows its own edge: %v %v", edges, decoys)
	}
}

func TestSingleInstanceHasNoPeers(t *testing.T) {
	edges, decoys := newTestManager(t, "only").peersOf(0)()
	if len(edges) != 0 || len(decoys) != 0 {
		t.Errorf("a lone instance has nothing to avoid: %v %v", edges, decoys)
	}
}

func TestStartRefusesAnInstanceWithoutConnect(t *testing.T) {
	m := newTestManager(t, "a", "b")
	if err := os.WriteFile(m.insts[1].applier.SnispoofConf, []byte("fake-sni = b.example\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := m.Start(context.Background()); err == nil {
		t.Error("an instance that can never be managed must fail loudly at start")
	}
}
