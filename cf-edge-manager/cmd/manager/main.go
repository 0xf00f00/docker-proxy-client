// Command manager is the cf-edge-manager worker: a control API + durable job
// queue that drives cfst to rank Cloudflare edges, a scheduled discovery scan,
// and the in-process runtime edge selector (the former picker + prober).
package main

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/robfig/cron/v3"

	"github.com/0xf00f00/cf-edge-manager/internal/config"
	"github.com/0xf00f00/cf-edge-manager/internal/manager"
	"github.com/0xf00f00/cf-edge-manager/internal/realpath"
	"github.com/0xf00f00/cf-edge-manager/internal/scanner"
)

func main() {
	log := slog.New(slog.NewJSONHandler(os.Stdout, nil))

	cfg, err := config.Load()
	if err != nil {
		log.Error("invalid configuration", "err", err)
		os.Exit(1)
	}

	svc, err := scanner.New(cfg, log)
	if err != nil {
		log.Error("initialization failed", "err", err)
		os.Exit(1)
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	svc.Start(ctx)

	// Scheduled discovery scan, evaluated in the container TZ; the dashboard can
	// still POST /scans on demand.
	if cfg.ScanCron != "" {
		c := cron.New(cron.WithSeconds(), cron.WithLocation(time.Local))
		if _, err := c.AddFunc(cfg.ScanCron, func() {
			log.Info("scheduled scan enqueued", "job", svc.EnqueueScan(), "cron", cfg.ScanCron)
		}); err != nil {
			log.Error("invalid SCAN_CRON; no scheduled scans", "cron", cfg.ScanCron, "err", err)
		} else {
			c.Start()
			defer c.Stop()
			log.Info("scan scheduler started", "cron", cfg.ScanCron, "tz", time.Local.String())
		}
	}

	// Runtime edge selection (the in-process picker+prober). Optional: off keeps
	// this a pure scanner. When on, it runs its select loop and exposes
	// GET /manager/status alongside the scanner's control API. Constructed before
	// the survival hook so an on-demand (dashboard) test can reuse its prober.
	var mgr *manager.Manager
	if cfg.SelectEnable {
		mgr = manager.New(cfg, log)
		if err := mgr.Start(ctx); err != nil {
			log.Error("manager start failed; continuing as scanner-only", "err", err)
			mgr = nil
		}
	}

	// Make the interactive "Test" run the real-path probe, tiered after cfst.
	svc.SetSurvivalProbe(buildSurvivalProbe(cfg, mgr, log))

	handler := svc.Handler()
	if mgr != nil {
		mux := http.NewServeMux()
		mux.HandleFunc("GET /manager/status", func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(mgr.Status())
		})
		mux.Handle("/", handler) // delegate everything else to the scanner API
		handler = mux
	}

	srv := &http.Server{
		Addr:              cfg.APIAddr,
		Handler:           handler,
		ReadHeaderTimeout: 5 * time.Second,
	}
	go func() {
		log.Info("control API listening", "addr", cfg.APIAddr)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Error("control API failed", "err", err)
			stop()
		}
	}()

	<-ctx.Done()
	log.Info("shutdown signal received")

	shutCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	_ = srv.Shutdown(shutCtx)

	if mgr != nil {
		mgr.Stop()
	}
	svc.Wait()
	log.Info("stopped")
}

// buildSurvivalProbe returns the test's real-path hook, or nil (cfst-only) when
// the identity wire params aren't set, so a bare `up` never errors. Selector on:
// reuse the manager's prober. Off: a strict per-test ephemeral prober.
func buildSurvivalProbe(cfg config.Config, mgr *manager.Manager, log *slog.Logger) scanner.SurvivalFunc {
	if !cfg.SurvivalCheck || cfg.ProbeUUID == "" || cfg.ProbeOriginHost == "" {
		log.Info("real-path test tier disabled; interactive tests are cfst-only",
			"probe_enable", cfg.SurvivalCheck, "have_uuid", cfg.ProbeUUID != "", "have_origin", cfg.ProbeOriginHost != "")
		return nil
	}
	if mgr != nil {
		log.Info("real-path test tier: reusing manager prober")
		return func(ctx context.Context, ip string) scanner.SurvivalResult {
			return toSurvival(mgr.ProbeSurvival(ctx, ip))
		}
	}
	log.Info("real-path test tier: ephemeral per-test prober")
	rpCfg := manager.ProberConfig(cfg)
	return func(ctx context.Context, ip string) scanner.SurvivalResult {
		p := realpath.New(rpCfg, log)
		if err := p.Start(); err != nil {
			return scanner.SurvivalResult{Err: "xray start: " + err.Error()}
		}
		defer p.Stop()
		return toSurvival(p.Probe(ctx, ip, realpath.Options{Force: true, Interactive: true}))
	}
}

func toSurvival(r realpath.Result) scanner.SurvivalResult {
	return scanner.SurvivalResult{
		Survived: r.Survived, FailRate: r.FailRate,
		Fails: r.Fails, Probes: r.Probes, Err: r.Err,
	}
}
