package api

import (
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/nikhilrkul/k8s-platform-lab/services/inventory/internal/store"
)

func newServer() http.Handler {
	s := &Server{
		Store:   store.New([]store.Item{{SKU: "BOOK-1", Available: 2, UnitPriceCents: 1250}}),
		Version: "test",
		Log:     slog.New(slog.NewTextHandler(io.Discard, nil)),
	}
	return s.Routes()
}

func do(t *testing.T, h http.Handler, method, path, body string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(method, path, strings.NewReader(body))
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec
}

func TestRoutes(t *testing.T) {
	h := newServer()
	tests := []struct {
		name, method, path, body string
		want                     int
	}{
		{"health", "GET", "/healthz", "", 200},
		{"ready", "GET", "/readyz", "", 200},
		{"stock", "GET", "/stock/BOOK-1", "", 200},
		{"stock missing", "GET", "/stock/NOPE", "", 404},
		{"reserve ok", "POST", "/reserve", `{"sku":"BOOK-1","quantity":1}`, 200},
		{"reserve too many", "POST", "/reserve", `{"sku":"BOOK-1","quantity":9}`, 409},
		{"reserve missing sku", "POST", "/reserve", `{"sku":"NOPE","quantity":1}`, 404},
		{"reserve zero", "POST", "/reserve", `{"sku":"BOOK-1","quantity":0}`, 400},
		{"reserve null quantity", "POST", "/reserve", `{"sku":"BOOK-1","quantity":null}`, 400},
		{"reserve no quantity", "POST", "/reserve", `{"sku":"BOOK-1"}`, 400},
		{"reserve unknown field", "POST", "/reserve", `{"sku":"BOOK-1","quantity":1,"x":1}`, 400},
		{"reserve bad json", "POST", "/reserve", `{`, 400},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			rec := do(t, h, tc.method, tc.path, tc.body)
			if rec.Code != tc.want {
				t.Fatalf("want %d, got %d: %s", tc.want, rec.Code, rec.Body.String())
			}
			if rec.Header().Get("X-Inventory-Version") != "test" {
				t.Fatal("missing version header")
			}
		})
	}
}
