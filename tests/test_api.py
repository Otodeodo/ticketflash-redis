from __future__ import annotations

import fakeredis
import pytest
from fastapi.testclient import TestClient

from app import api
from app.db import Database


@pytest.fixture
def client(monkeypatch):
    fake = Database(fakeredis.FakeRedis(decode_responses=True))
    api.app.dependency_overrides[api.db] = lambda: fake
    yield TestClient(api.app)
    api.app.dependency_overrides.clear()


def test_full_flow(client):
    user = client.post("/users", json={"name": "Ana", "email": "ana@example.com"}).json()
    ev = client.post("/events", json={"name": "Show", "venue": "Sala", "date": "2026-12-01T20:00",
                                      "category": "musica", "price": 10, "capacity": 5}).json()
    token = client.post("/auth/login", json={"user_id": user["id"]}).json()["token"]
    headers = {"X-Session-Token": token}

    assert client.post("/holds", json={"user_id": user["id"], "event_id": ev["id"], "qty": 9},
                       headers=headers).status_code == 409
    hold = client.post("/holds", json={"user_id": user["id"], "event_id": ev["id"], "qty": 2},
                       headers=headers)
    assert hold.status_code == 201
    assert client.get(f"/events/{ev['id']}").json()["stock"] == 3

    order = client.post(f"/holds/{hold.json()['id']}/confirm", headers=headers)
    assert order.status_code == 201 and order.json()["total"] == 20.0
    assert client.get("/ranking").json()[0]["sold"] == 2
    assert client.get(f"/users/{user['id']}/orders").json()[0]["id"] == order.json()["id"]

    assert client.post("/auth/logout", headers=headers).json()["logged_out"] is True
    assert client.get("/auth/me", headers=headers).status_code == 401
    assert client.get("/events/999").status_code == 404
