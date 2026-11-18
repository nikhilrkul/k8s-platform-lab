// Command inventory serves stock levels to the orders service.
package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/nikhilrkul/k8s-platform-lab/services/inventory/internal/api"
	"github.com/nikhilrkul/k8s-platform-lab/services/inventory/internal/store"
)

func main() {
	log := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	if err := run(log); err != nil {
		log.Error("fatal", "err", err)
		os.Exit(1)
	}
}

func run(log *slog.Logger) error {
	items, err := store.ParseSeed(os.Getenv("SEED_STOCK"))
	if err != nil {
		return err // fail fast: a bad ConfigMap should crash the pod, not serve empty stock
	}
	port := getenv("PORT", "8080")
	srv := &http.Server{
		Addr:              ":" + port,
		Handler:           (&api.Server{Store: store.New(items), Version: getenv("APP_VERSION", "dev"), Log: log}).Routes(),
		ReadHeaderTimeout: 5 * time.Second,
	}

	// Graceful shutdown: Kubernetes sends SIGTERM, then waits
	// terminationGracePeriodSeconds before SIGKILL.
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, os.Interrupt)
	defer stop()
	errCh := make(chan error, 1)
	go func() {
		log.Info("listening", "port", port, "skus", len(items))
		errCh <- srv.ListenAndServe()
	}()
	select {
	case err := <-errCh:
		if !errors.Is(err, http.ErrServerClosed) {
			return err
		}
	case <-ctx.Done():
		log.Info("shutting down")
	}
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	return srv.Shutdown(shutdownCtx)
}

func getenv(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}
