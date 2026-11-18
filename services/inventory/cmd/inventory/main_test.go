package main

import (
	"io"
	"log/slog"
	"net"
	"net/http"
	"strconv"
	"syscall"
	"testing"
	"time"
)

func TestRunFailsFastOnBadSeed(t *testing.T) {
	t.Setenv("SEED_STOCK", "")
	if err := run(slog.New(slog.NewTextHandler(io.Discard, nil))); err == nil {
		t.Fatal("expected error for empty seed")
	}
}

func TestRunServesAndShutsDownOnSIGTERM(t *testing.T) {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	port := l.Addr().(*net.TCPAddr).Port
	_ = l.Close()
	t.Setenv("SEED_STOCK", "A:1:1")
	t.Setenv("PORT", strconv.Itoa(port))

	done := make(chan error, 1)
	go func() { done <- run(slog.New(slog.NewTextHandler(io.Discard, nil))) }()

	url := "http://127.0.0.1:" + strconv.Itoa(port) + "/healthz"
	var ok bool
	for range 50 {
		resp, err := http.Get(url)
		if err == nil {
			_ = resp.Body.Close()
			ok = resp.StatusCode == http.StatusOK
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	if !ok {
		t.Fatal("server never became healthy")
	}
	_ = syscall.Kill(syscall.Getpid(), syscall.SIGTERM)
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("shutdown error: %v", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("did not shut down")
	}
}

func TestGetenv(t *testing.T) {
	t.Setenv("X_TEST", "")
	if getenv("X_TEST", "d") != "d" {
		t.Fatal("default not used")
	}
	t.Setenv("X_TEST", "v")
	if getenv("X_TEST", "d") != "v" {
		t.Fatal("value not used")
	}
}
