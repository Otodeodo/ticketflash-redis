from __future__ import annotations

import pytest

from app import keys
from app.repositories import (AnalyticsRepository, EventRepository, HoldRepository,
                              OrderRepository, SessionRepository, UserRepository)
from app.repositories.common import ConflictError, NotFoundError


def _event(db, capacity=10, price=20.0, category="musica"):
    return EventRepository(db).create(name="Show", venue="Sala", date="2026-12-01T20:00",
                                      category=category, price=price, capacity=capacity)


def _user(db):
    return UserRepository(db).create(name="Ana", email="ana@example.com")


def test_event_crud(db):
    repo = EventRepository(db)
    ev = _event(db)
    assert ev["stock"] == 10 and ev["sold"] == 0
    assert repo.get(ev["id"])["name"] == "Show"
    repo.update(ev["id"], name="Show 2", category="teatro")
    assert repo.get(ev["id"])["name"] == "Show 2"
    assert repo.list_by_category("teatro")[0]["id"] == ev["id"]
    assert repo.list_by_category("musica") == []
    repo.delete(ev["id"])
    with pytest.raises(NotFoundError):
        repo.get(ev["id"])
    assert repo.list_all() == []


def test_session_ttl(db):
    user = _user(db)
    sess = SessionRepository(db).login(user["id"])
    ttl = db.client.ttl(keys.session(sess["token"]))
    assert 0 < ttl <= sess["ttl_seconds"]
    assert SessionRepository(db).touch(sess["token"])["user_id"] == user["id"]
    assert SessionRepository(db).logout(sess["token"])
    with pytest.raises(NotFoundError):
        SessionRepository(db).touch(sess["token"])


def test_hold_decrements_stock_atomically(db):
    user, ev = _user(db), _event(db, capacity=5)
    holds = HoldRepository(db)
    hold = holds.create(user_id=user["id"], event_id=ev["id"], qty=3)
    assert EventRepository(db).get(ev["id"])["stock"] == 2
    assert hold["ttl_seconds"] > 0
    with pytest.raises(ConflictError):
        holds.create(user_id=user["id"], event_id=ev["id"], qty=3)
    assert EventRepository(db).get(ev["id"])["stock"] == 2


def test_cancel_hold_restores_stock(db):
    user, ev = _user(db), _event(db, capacity=5)
    holds = HoldRepository(db)
    hold = holds.create(user_id=user["id"], event_id=ev["id"], qty=2)
    assert holds.cancel(hold["id"])["released"] is True
    assert EventRepository(db).get(ev["id"])["stock"] == 5
    assert db.client.zcard(keys.holds_expiring()) == 0


def test_expired_hold_is_reclaimed(db):
    user, ev = _user(db), _event(db, capacity=5)
    holds = HoldRepository(db)
    hold = holds.create(user_id=user["id"], event_id=ev["id"], qty=4)
    # Simular el paso del tiempo: Redis expira la clave y el score queda en el pasado.
    db.client.delete(keys.hold(hold["id"]))
    member = keys.hold_member(hold["id"], ev["id"], user["id"], 4)
    db.client.zadd(keys.holds_expiring(), {member: 1})
    assert holds.reclaim_expired() == 1
    assert EventRepository(db).get(ev["id"])["stock"] == 5
    assert holds.reclaim_expired() == 0  # no se devuelve dos veces


def test_confirm_creates_order_and_ranking(db):
    user, ev = _user(db), _event(db, capacity=5, price=10.0)
    hold = HoldRepository(db).create(user_id=user["id"], event_id=ev["id"], qty=2)
    order = OrderRepository(db).confirm(hold["id"])
    assert order["total"] == 20.0 and order["status"] == "CONFIRMED"
    with pytest.raises(NotFoundError):
        HoldRepository(db).get(hold["id"])
    top = AnalyticsRepository(db).top_events()
    assert top[0] == {"event_id": ev["id"], "name": "Show", "sold": 2}
    assert OrderRepository(db).list_for_user(user["id"])[0]["id"] == order["id"]
    assert AnalyticsRepository(db).recent_activity()[0]["order_id"] == order["id"]


def test_cancel_order_restores_stock_and_ranking(db):
    user, ev = _user(db), _event(db, capacity=5)
    hold = HoldRepository(db).create(user_id=user["id"], event_id=ev["id"], qty=2)
    order = OrderRepository(db).confirm(hold["id"])
    OrderRepository(db).cancel(order["id"])
    assert EventRepository(db).get(ev["id"])["stock"] == 5
    assert AnalyticsRepository(db).top_events()[0]["sold"] == 0
    with pytest.raises(ConflictError):
        OrderRepository(db).cancel(order["id"])


def test_summary_cache(db):
    ev = _event(db)
    _, hit1 = EventRepository(db).summary_cached(ev["id"])
    _, hit2 = EventRepository(db).summary_cached(ev["id"])
    assert (hit1, hit2) == (False, True)
    assert db.client.ttl(keys.event_summary_cache(ev["id"])) > 0


def test_unique_visitors_hll(db):
    ev = _event(db)
    repo = EventRepository(db)
    for v in ["a", "b", "a", "c", "b"]:
        repo.get(ev["id"], track_visitor=v)
    assert repo.get(ev["id"])["unique_visitors"] == 3
