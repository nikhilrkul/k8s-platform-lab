// Package api exposes the inventory store over HTTP.
package api

import (
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"

	"github.com/nikhilrkul/k8s-platform-lab/services/inventory/internal/store"
)

// Server wires HTTP routes to the store.
type Server struct {
	Store   *store.Store
	Version string
	Log     *slog.Logger
}

type reserveRequest struct {
	SKU      string `json:"sku"`
	Quantity *int   `json:"quantity"` // pointer so a missing field is distinguishable from 0
}

// Routes returns the HTTP handler.
func (s *Server) Routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		s.json(w, http.StatusOK, map[string]string{"status": "ok"})
	})
	mux.HandleFunc("GET /readyz", func(w http.ResponseWriter, _ *http.Request) {
		s.json(w, http.StatusOK, map[string]string{"status": "ready"})
	})
	mux.HandleFunc("GET /stock/{sku}", s.getStock)
	mux.HandleFunc("POST /reserve", s.reserve)
	return s.withVersion(mux)
}

// withVersion stamps every response so you can watch Istio split traffic
// between the stable and canary Deployments.
func (s *Server) withVersion(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Inventory-Version", s.Version)
		next.ServeHTTP(w, r)
	})
}

func (s *Server) getStock(w http.ResponseWriter, r *http.Request) {
	it, err := s.Store.Get(r.PathValue("sku"))
	if errors.Is(err, store.ErrNotFound) {
		s.json(w, http.StatusNotFound, map[string]string{"error": err.Error()})
		return
	}
	s.json(w, http.StatusOK, it)
}

func (s *Server) reserve(w http.ResponseWriter, r *http.Request) {
	var req reserveRequest
	dec := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<16))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&req); err != nil || req.SKU == "" || req.Quantity == nil {
		s.json(w, http.StatusBadRequest, map[string]string{"error": "sku and quantity are required"})
		return
	}
	it, err := s.Store.Reserve(req.SKU, *req.Quantity)
	switch {
	case errors.Is(err, store.ErrNotFound):
		s.json(w, http.StatusNotFound, map[string]string{"error": err.Error()})
	case errors.Is(err, store.ErrInsufficient):
		s.json(w, http.StatusConflict, map[string]string{"error": err.Error()})
	case errors.Is(err, store.ErrInvalidQuantity):
		s.json(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
	case err != nil:
		s.json(w, http.StatusInternalServerError, map[string]string{"error": "internal"})
	default:
		s.Log.Info("reserved", "sku", it.SKU, "qty", *req.Quantity, "left", it.Available)
		s.json(w, http.StatusOK, it)
	}
}

func (s *Server) json(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	if err := json.NewEncoder(w).Encode(v); err != nil {
		s.Log.Error("encode response", "err", err)
	}
}
