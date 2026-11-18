"""Orders API: takes an order, checks and reserves stock in the inventory service."""

from __future__ import annotations

import hmac
import logging
import os
import uuid
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from app.config import Settings
from app.inventory_client import InventoryClient, InventoryError, OutOfStockError

log = logging.getLogger("orders")


class OrderRequest(BaseModel):
    sku: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z0-9-]+$")
    quantity: int = Field(gt=0, le=100)


class Order(BaseModel):
    id: str
    sku: str
    quantity: int
    total_cents: int
    discount_applied: bool
    version: str


def apply_discount(total_cents: int, settings: Settings) -> tuple[int, bool]:
    """Return (new_total, applied). Integer maths only: money must never be a float."""
    if not settings.feature_discounts or settings.discount_percent == 0:
        return total_cents, False
    discounted = total_cents * (100 - settings.discount_percent) // 100
    return discounted, True


def create_app(settings: Settings | None = None, client: InventoryClient | None = None) -> FastAPI:
    cfg = settings or Settings.from_env()
    inventory = client or InventoryClient(cfg.inventory_url, cfg.request_timeout_ms)
    logging.basicConfig(level=cfg.log_level)
    orders: dict[str, Order] = {}
    version = os.environ.get("APP_VERSION", "dev")

    @asynccontextmanager
    async def lifespan(_: FastAPI):  # type: ignore[no-untyped-def]
        yield
        inventory.close()

    app = FastAPI(title="orders", version=version, lifespan=lifespan)

    def check_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
        # Constant-time compare so the key can't be guessed from response timing.
        if x_api_key is None or not hmac.compare_digest(x_api_key, cfg.api_key):
            raise HTTPException(status_code=401, detail="invalid api key")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        """Liveness: the process is up. Never call dependencies here."""
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> dict[str, str]:
        """Readiness: can we serve traffic? Checks the dependency."""
        try:
            inventory.get_stock("HEALTHCHECK")
        except OutOfStockError:
            pass  # 404 still proves inventory is reachable
        except InventoryError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"status": "ready"}

    @app.post("/orders", status_code=201, dependencies=[Depends(check_api_key)])
    def create_order(req: OrderRequest) -> Order:
        try:
            stock = inventory.get_stock(req.sku)
            if stock.available < req.quantity:
                raise HTTPException(status_code=409, detail="insufficient stock")
            inventory.reserve(req.sku, req.quantity)
        except OutOfStockError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except InventoryError as exc:
            log.error("inventory failure: %s", exc)
            raise HTTPException(status_code=502, detail="inventory unavailable") from exc

        total, applied = apply_discount(stock.unit_price_cents * req.quantity, cfg)
        order = Order(
            id=str(uuid.uuid4()),
            sku=req.sku,
            quantity=req.quantity,
            total_cents=total,
            discount_applied=applied,
            version=version,
        )
        orders[order.id] = order
        return order

    @app.get("/orders/{order_id}", dependencies=[Depends(check_api_key)])
    def get_order(order_id: str) -> Order:
        order = orders.get(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="order not found")
        return order

    return app


def _app_factory() -> FastAPI:  # pragma: no cover - used by uvicorn --factory
    return create_app()
