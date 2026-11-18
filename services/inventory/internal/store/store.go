// Package store is a concurrency-safe, in-memory stock ledger.
package store

import (
	"errors"
	"fmt"
	"strconv"
	"strings"
	"sync"
)

var (
	// ErrNotFound means the SKU does not exist.
	ErrNotFound = errors.New("sku not found")
	// ErrInsufficient means there is not enough stock to reserve.
	ErrInsufficient = errors.New("insufficient stock")
	// ErrInvalidQuantity means a non-positive quantity was requested.
	ErrInvalidQuantity = errors.New("quantity must be positive")
)

// Item is one SKU's stock level. Prices are integer cents: never floats for money.
type Item struct {
	SKU            string `json:"sku"`
	Available      int    `json:"available"`
	UnitPriceCents int    `json:"unit_price_cents"`
}

// Store holds items behind a mutex.
type Store struct {
	mu    sync.Mutex
	items map[string]Item
}

// New returns a store seeded with items.
func New(items []Item) *Store {
	m := make(map[string]Item, len(items))
	for _, it := range items {
		m[it.SKU] = it
	}
	return &Store{items: m}
}

// Get returns a copy of the item.
func (s *Store) Get(sku string) (Item, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	it, ok := s.items[sku]
	if !ok {
		return Item{}, ErrNotFound
	}
	return it, nil
}

// Reserve atomically decrements stock.
func (s *Store) Reserve(sku string, qty int) (Item, error) {
	if qty <= 0 {
		return Item{}, ErrInvalidQuantity
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	it, ok := s.items[sku]
	if !ok {
		return Item{}, ErrNotFound
	}
	if it.Available < qty {
		return Item{}, ErrInsufficient
	}
	it.Available -= qty
	s.items[sku] = it
	return it, nil
}

// ParseSeed parses "SKU:available:priceCents,SKU2:..." as delivered by a ConfigMap.
// It rejects empty fields and negative numbers rather than silently using zero.
func ParseSeed(raw string) ([]Item, error) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return nil, errors.New("seed is empty")
	}
	entries := strings.Split(raw, ",")
	items := make([]Item, 0, len(entries))
	for _, entry := range entries {
		parts := strings.Split(strings.TrimSpace(entry), ":")
		if len(parts) != 3 || parts[0] == "" {
			return nil, fmt.Errorf("bad seed entry %q (want SKU:available:priceCents)", entry)
		}
		avail, err := strconv.Atoi(parts[1])
		if err != nil || avail < 0 {
			return nil, fmt.Errorf("bad available count in %q", entry)
		}
		price, err := strconv.Atoi(parts[2])
		if err != nil || price <= 0 {
			return nil, fmt.Errorf("bad price in %q", entry)
		}
		items = append(items, Item{SKU: parts[0], Available: avail, UnitPriceCents: price})
	}
	return items, nil
}
