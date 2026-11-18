package store

import (
	"errors"
	"sync"
	"testing"
)

func newStore() *Store {
	return New([]Item{{SKU: "BOOK-1", Available: 3, UnitPriceCents: 1250}})
}

func TestGet(t *testing.T) {
	s := newStore()
	it, err := s.Get("BOOK-1")
	if err != nil || it.Available != 3 {
		t.Fatalf("got %+v, %v", it, err)
	}
	if _, err := s.Get("NOPE"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("want ErrNotFound, got %v", err)
	}
}

func TestReserve(t *testing.T) {
	s := newStore()
	tests := []struct {
		name string
		sku  string
		qty  int
		want error
	}{
		{"ok", "BOOK-1", 2, nil},
		{"insufficient", "BOOK-1", 5, ErrInsufficient},
		{"missing", "NOPE", 1, ErrNotFound},
		{"zero", "BOOK-1", 0, ErrInvalidQuantity},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if _, err := s.Reserve(tc.sku, tc.qty); !errors.Is(err, tc.want) {
				t.Fatalf("want %v, got %v", tc.want, err)
			}
		})
	}
}

func TestReserveIsConcurrencySafe(t *testing.T) {
	s := New([]Item{{SKU: "A", Available: 100, UnitPriceCents: 1}})
	var wg sync.WaitGroup
	for range 150 {
		wg.Add(1)
		go func() { defer wg.Done(); _, _ = s.Reserve("A", 1) }()
	}
	wg.Wait()
	if it, _ := s.Get("A"); it.Available != 0 {
		t.Fatalf("oversold: %d left", it.Available)
	}
}

func TestParseSeed(t *testing.T) {
	items, err := ParseSeed("BOOK-1:5:1250, PEN-2:10:99")
	if err != nil || len(items) != 2 || items[1].UnitPriceCents != 99 {
		t.Fatalf("got %+v, %v", items, err)
	}
	for _, bad := range []string{"", "A:1", ":1:1", "A:x:1", "A:-1:1", "A:1:0", "A:1:null"} {
		if _, err := ParseSeed(bad); err == nil {
			t.Errorf("expected error for %q", bad)
		}
	}
}
