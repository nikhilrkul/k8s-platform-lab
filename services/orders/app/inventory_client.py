"""HTTP client for the Go inventory service.

Inside the mesh this calls http://inventory.<namespace>.svc.cluster.local.
The Istio sidecar upgrades the connection to mutual TLS transparently, and
applies the retries and timeouts set in the VirtualService, so the code
stays plain HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx


class InventoryError(RuntimeError):
    """Inventory is unreachable or returned something unexpected."""


class OutOfStockError(RuntimeError):
    """Requested quantity is not available."""


@dataclass(frozen=True)
class StockLevel:
    sku: str
    available: int
    unit_price_cents: int


class InventoryClient:
    def __init__(
        self, base_url: str, timeout_ms: int, transport: httpx.BaseTransport | None = None
    ):
        self._client = httpx.Client(
            base_url=base_url, timeout=timeout_ms / 1000, transport=transport
        )

    def close(self) -> None:
        self._client.close()

    def get_stock(self, sku: str) -> StockLevel:
        try:
            resp = self._client.get(f"/stock/{sku}")
        except httpx.HTTPError as exc:
            raise InventoryError(f"inventory unreachable: {exc}") from exc
        if resp.status_code == 404:
            raise OutOfStockError(f"unknown sku {sku}")
        if resp.status_code != 200:
            raise InventoryError(f"inventory returned {resp.status_code}")
        body = resp.json()
        # Validate the payload instead of trusting it: a null price must never
        # turn into a free order.
        price = body.get("unit_price_cents")
        available = body.get("available")
        if not isinstance(price, int) or not isinstance(available, int):
            raise InventoryError(f"malformed inventory payload: {body!r}")
        return StockLevel(sku=sku, available=available, unit_price_cents=price)

    def reserve(self, sku: str, quantity: int) -> None:
        try:
            resp = self._client.post("/reserve", json={"sku": sku, "quantity": quantity})
        except httpx.HTTPError as exc:
            raise InventoryError(f"inventory unreachable: {exc}") from exc
        if resp.status_code == 409:
            raise OutOfStockError(f"not enough stock for {sku}")
        if resp.status_code != 200:
            raise InventoryError(f"reserve failed with {resp.status_code}")
