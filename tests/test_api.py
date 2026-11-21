from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api import app, stores as stores_dep
from app.repositories import UserRepository

PASSWORD = "clave-segura-123"


@pytest.fixture
def client(stores):
    app.dependency_overrides[stores_dep] = lambda: stores
    UserRepository(stores).create(name="Admin", email="admin@example.com", password=PASSWORD, role="admin")
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client, email, password=PASSWORD):
    r = client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"X-Session-Token": r.json()["token"]}


def test_health_reports_both_databases(client):
    body = client.get("/health").json()
    assert body["redis_ok"] and body["postgres_ok"] and body["ok"]
    assert body["pg_user"] == "tf_app"


def test_register_login_and_me(client):
    r = client.post("/auth/register", json={"name": "Ana", "email": "ana@example.com", "password": PASSWORD})
    assert r.status_code == 201 and r.json()["role"] == "customer"
    headers = {"X-Session-Token": r.json()["token"]}
    assert client.get("/auth/me", headers=headers).json()["user"]["email"] == "ana@example.com"
    assert client.post("/auth/register", json={"name": "Ana", "email": "ana@example.com",
                                               "password": PASSWORD}).status_code == 409
    bad = client.post("/auth/login", json={"email": "ana@example.com", "password": "incorrecta1"})
    assert bad.status_code == 401


def test_login_is_rate_limited(client):
    for _ in range(5):
        client.post("/auth/login", json={"email": "admin@example.com", "password": "incorrecta1"})
    r = client.post("/auth/login", json={"email": "admin@example.com", "password": PASSWORD})
    assert r.status_code == 429


def test_customer_cannot_manage_events_but_admin_can(client):
    customer = client.post("/auth/register", json={"name": "Ana", "email": "ana@example.com", "password": PASSWORD}).json()
    c_headers = {"X-Session-Token": customer["token"]}
    body = {"name": "Show", "venue": "Sala", "date": "2026-12-05T20:00", "category": "musica",
            "price": 10, "capacity": 5}
    assert client.post("/events", json=body, headers=c_headers).status_code == 403
    assert client.post("/events", json=body).status_code == 422          # sin token
    a_headers = _login(client, "admin@example.com")
    created = client.post("/events", json=body, headers=a_headers)
    assert created.status_code == 201 and created.json()["stock"] == 5
    assert client.get("/admin/sales", headers=c_headers).status_code == 403
    assert client.get("/admin/sales", headers=a_headers).status_code == 200


def test_purchase_flow_and_ownership(client):
    admin = _login(client, "admin@example.com")
    ev = client.post("/events", headers=admin, json={"name": "Show", "venue": "Sala", "date": "2026-12-05T20:00",
                                                      "category": "musica", "price": 10, "capacity": 5}).json()
    ana = client.post("/auth/register", json={"name": "Ana", "email": "ana@example.com", "password": PASSWORD}).json()
    luis = client.post("/auth/register", json={"name": "Luis", "email": "luis@example.com", "password": PASSWORD}).json()
    ana_h, luis_h = {"X-Session-Token": ana["token"]}, {"X-Session-Token": luis["token"]}

    hold = client.post("/holds", headers=ana_h, json={"user_id": ana["user_id"], "event_id": ev["id"], "qty": 2})
    assert hold.status_code == 201
    assert client.get(f"/events/{ev['id']}").json()["stock"] == 3
    # Luis no puede usar la reserva de Ana ni reservar a nombre de ella.
    assert client.post(f"/holds/{hold.json()['id']}/confirm", headers=luis_h).status_code == 403
    assert client.post("/holds", headers=luis_h, json={"user_id": ana["user_id"], "event_id": ev["id"], "qty": 1}).status_code == 403
    order = client.post(f"/holds/{hold.json()['id']}/confirm", headers=ana_h)
    assert order.status_code == 201 and order.json()["total"] == 20.0
    assert client.get(f"/users/{ana['user_id']}/orders", headers=luis_h).status_code == 403
    assert len(client.get(f"/users/{ana['user_id']}/orders", headers=ana_h).json()) == 1
    assert client.post(f"/orders/{order.json()['id']}/cancel", headers=luis_h).status_code == 403
    assert client.post(f"/orders/{order.json()['id']}/cancel", headers=ana_h).json()["status"] == "CANCELLED"
    assert client.get("/admin/consistency", headers=admin).json()["ok"] is True
    assert client.get("/ranking").json() == []


def test_optimistic_lock_via_api(client):
    admin = _login(client, "admin@example.com")
    ev = client.post("/events", headers=admin, json={"name": "Show", "venue": "Sala", "date": "2026-12-05T20:00",
                                                      "category": "musica", "price": 10, "capacity": 5}).json()
    ok = client.patch(f"/events/{ev['id']}", headers=admin, json={"price": 12, "version": ev["version"]})
    stale = client.patch(f"/events/{ev['id']}", headers=admin, json={"price": 99, "version": ev["version"]})
    assert ok.status_code == 200 and stale.status_code == 409


def test_invalid_date_is_422(client):
    admin = _login(client, "admin@example.com")
    r = client.post("/events", headers=admin, json={"name": "X", "venue": "Y", "date": "mañana",
                                                    "category": "musica", "price": 10, "capacity": 5})
    assert r.status_code == 422


def test_reconcile_endpoint_repairs_redis(client, stores):
    from app import keys
    admin = _login(client, "admin@example.com")
    ev = client.post("/events", headers=admin, json={"name": "Show", "venue": "Sala", "date": "2026-12-05T20:00",
                                                      "category": "musica", "price": 10, "capacity": 5}).json()
    stores.redis.client.set(keys.event_stock(ev["id"]), 999)      # corrupción deliberada
    assert client.get("/admin/consistency", headers=admin).json()["ok"] is False
    assert client.post("/admin/reconcile", headers=admin).json()["now_consistent"] is True
