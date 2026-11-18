import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.inventory_client import InventoryClient
from app.main import apply_discount, create_app

KEY = {"X-API-Key": "s3cret"}


def make_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "inventory_url": "http://inventory",
        "api_key": "s3cret",
        "feature_discounts": False,
        "discount_percent": 0,
        "request_timeout_ms": 1000,
        "log_level": "INFO",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def fake_inventory(stock: dict[str, dict[str, object]], fail: bool = False) -> InventoryClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if fail:
            raise httpx.ConnectError("boom", request=request)
        if request.method == "GET" and request.url.path.startswith("/stock/"):
            sku = request.url.path.rsplit("/", 1)[-1]
            if sku not in stock:
                return httpx.Response(404)
            return httpx.Response(200, json={"sku": sku, **stock[sku]})
        if request.method == "POST" and request.url.path == "/reserve":
            body = json.loads(request.content)
            item = stock[body["sku"]]
            if int(str(item["available"])) < body["quantity"]:
                return httpx.Response(409)
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(500)

    return InventoryClient("http://inventory", 1000, transport=httpx.MockTransport(handler))


@pytest.fixture
def client() -> TestClient:
    inv = fake_inventory({"BOOK-1": {"available": 5, "unit_price_cents": 1250}})
    return TestClient(create_app(make_settings(), inv))


def test_health(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_ready(client: TestClient) -> None:
    assert client.get("/readyz").status_code == 200


def test_not_ready_when_inventory_down() -> None:
    app = create_app(make_settings(), fake_inventory({}, fail=True))
    assert TestClient(app).get("/readyz").status_code == 503


def test_create_and_get_order(client: TestClient) -> None:
    resp = client.post("/orders", json={"sku": "BOOK-1", "quantity": 2}, headers=KEY)
    assert resp.status_code == 201
    order = resp.json()
    assert order["total_cents"] == 2500
    assert order["discount_applied"] is False
    fetched = client.get(f"/orders/{order['id']}", headers=KEY)
    assert fetched.json() == order


def test_requires_api_key(client: TestClient) -> None:
    assert client.post("/orders", json={"sku": "BOOK-1", "quantity": 1}).status_code == 401
    bad = {"X-API-Key": "wrong"}
    assert (
        client.post("/orders", json={"sku": "BOOK-1", "quantity": 1}, headers=bad).status_code
        == 401
    )


def test_insufficient_stock(client: TestClient) -> None:
    resp = client.post("/orders", json={"sku": "BOOK-1", "quantity": 9}, headers=KEY)
    assert resp.status_code == 409


def test_unknown_sku(client: TestClient) -> None:
    resp = client.post("/orders", json={"sku": "NOPE", "quantity": 1}, headers=KEY)
    assert resp.status_code == 409


def test_inventory_down_returns_502() -> None:
    app = create_app(make_settings(), fake_inventory({}, fail=True))
    resp = TestClient(app).post("/orders", json={"sku": "BOOK-1", "quantity": 1}, headers=KEY)
    assert resp.status_code == 502


def test_null_price_is_rejected_not_free() -> None:
    inv = fake_inventory({"BOOK-1": {"available": 5, "unit_price_cents": None}})
    app = create_app(make_settings(), inv)
    resp = TestClient(app).post("/orders", json={"sku": "BOOK-1", "quantity": 1}, headers=KEY)
    assert resp.status_code == 502


def test_validation_rejects_bad_input(client: TestClient) -> None:
    assert (
        client.post("/orders", json={"sku": "book 1", "quantity": 1}, headers=KEY).status_code
        == 422
    )
    assert (
        client.post("/orders", json={"sku": "BOOK-1", "quantity": 0}, headers=KEY).status_code
        == 422
    )


def test_missing_order(client: TestClient) -> None:
    assert client.get("/orders/nope", headers=KEY).status_code == 404


def test_discount_math() -> None:
    on = make_settings(feature_discounts=True, discount_percent=10)
    assert apply_discount(1999, on) == (1799, True)
    assert apply_discount(1999, make_settings()) == (1999, False)
    assert apply_discount(1999, make_settings(feature_discounts=True)) == (1999, False)
