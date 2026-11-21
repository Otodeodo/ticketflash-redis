from __future__ import annotations

import pytest

from app import keys
from app.repositories import (AnalyticsRepository, EventRepository, HoldRepository,
                              MaintenanceService, OrderRepository, SessionRepository,
                              UserRepository)
from app.repositories.common import (ConflictError, ForbiddenError, InvalidCredentials,
                                     NotFoundError, TooManyAttempts)

PASSWORD = "clave-segura-123"


def _event(stores, capacity=10, price=20.0, category="musica", name="Show"):
    return EventRepository(stores).create(name=name, venue="Sala", date="2026-12-01T20:00",
                                          category=category, price=price, capacity=capacity)


def _user(stores, email="ana@example.com", role="customer"):
    return UserRepository(stores).create(name="Ana", email=email, password=PASSWORD, role=role)


# --------------------------------------------------------------------- usuarios
def test_user_create_and_authenticate(stores):
    user = _user(stores)
    assert user["role"] == "customer" and "password_hash" not in user
    assert UserRepository(stores).authenticate("ANA@example.com", PASSWORD)["id"] == user["id"]
    with pytest.raises(InvalidCredentials):
        UserRepository(stores).authenticate("ana@example.com", "otra-clave")
    with pytest.raises(InvalidCredentials):
        UserRepository(stores).authenticate("nadie@example.com", PASSWORD)


def test_password_is_hashed_in_postgres(stores):
    _user(stores)
    row = stores.pg.fetch_one("SELECT password_hash FROM users")
    assert row["password_hash"].startswith("scrypt$") and PASSWORD not in row["password_hash"]


def test_duplicate_email_rejected_case_insensitive(stores):
    _user(stores, email="ana@example.com")
    with pytest.raises(ConflictError):
        _user(stores, email="ANA@Example.com")


def test_short_password_rejected(stores):
    with pytest.raises(ConflictError):
        UserRepository(stores).create(name="X", email="x@example.com", password="corta")


def test_login_lockout_after_failed_attempts(stores):
    _user(stores)
    repo = UserRepository(stores)
    for _ in range(5):
        with pytest.raises(InvalidCredentials):
            repo.authenticate("ana@example.com", "mala")
    with pytest.raises(TooManyAttempts):          # ya bloqueado, incluso con la clave correcta
        repo.authenticate("ana@example.com", PASSWORD)
    assert stores.redis.client.ttl(keys.login_attempts("ana@example.com")) > 0


def test_session_ttl_and_role(stores):
    user = _user(stores, role="admin")
    sess = SessionRepository(stores).login(user)
    assert 0 < stores.redis.client.ttl(keys.session(sess["token"])) <= sess["ttl_seconds"]
    assert SessionRepository(stores).touch(sess["token"])["role"] == "admin"
    assert SessionRepository(stores).logout(sess["token"])
    with pytest.raises(NotFoundError):
        SessionRepository(stores).touch(sess["token"])


# ---------------------------------------------------------------------- eventos
def test_event_crud(stores):
    repo = EventRepository(stores)
    ev = _event(stores)
    assert ev["stock"] == 10 and ev["sold"] == 0 and ev["version"] == 1
    assert repo.get(ev["id"])["name"] == "Show"
    updated = repo.update(ev["id"], name="Show 2", category="teatro")
    assert updated["name"] == "Show 2" and updated["version"] == 2
    assert repo.list_by_category("teatro")[0]["id"] == ev["id"]
    assert repo.list_by_category("musica") == []
    assert repo.categories() == ["teatro"]
    repo.delete(ev["id"])
    with pytest.raises(NotFoundError):
        repo.get(ev["id"])
    assert repo.list_all() == []
    assert stores.redis.client.exists(keys.event_stock(ev["id"])) == 0


def test_optimistic_locking_detects_concurrent_edit(stores):
    repo = EventRepository(stores)
    ev = _event(stores)
    repo.update(ev["id"], version=ev["version"], price=30.0)          # primer admin: gana
    with pytest.raises(ConflictError):                                # segundo admin: versión vieja
        repo.update(ev["id"], version=ev["version"], price=99.0)
    assert repo.get(ev["id"])["price"] == 30.0


def test_unknown_or_malformed_ids(stores):
    with pytest.raises(NotFoundError):
        EventRepository(stores).get("999")
    with pytest.raises(NotFoundError):
        EventRepository(stores).get("no-es-un-id")


def test_add_stock_updates_both_databases(stores):
    repo = EventRepository(stores)
    ev = _event(stores, capacity=5)
    assert repo.add_stock(ev["id"], 10) == 15
    got = repo.get(ev["id"])
    assert got["capacity"] == 15 and got["stock"] == 15
    with pytest.raises(ConflictError):
        repo.add_stock(ev["id"], -100)
    assert repo.get(ev["id"])["stock"] == 15      # Redis no quedó modificado


def test_summary_cache(stores):
    ev = _event(stores)
    _, hit1 = EventRepository(stores).summary_cached(ev["id"])
    _, hit2 = EventRepository(stores).summary_cached(ev["id"])
    assert (hit1, hit2) == (False, True)
    assert stores.redis.client.ttl(keys.event_summary_cache(ev["id"])) > 0


def test_unique_visitors_hll(stores):
    ev = _event(stores)
    repo = EventRepository(stores)
    for v in ["a", "b", "a", "c", "b"]:
        repo.get(ev["id"], track_visitor=v)
    assert repo.get(ev["id"])["unique_visitors"] == 3


# --------------------------------------------------------------------- reservas
def test_hold_decrements_stock_atomically(stores):
    user, ev = _user(stores), _event(stores, capacity=5)
    holds = HoldRepository(stores)
    hold = holds.create(user_id=user["id"], event_id=ev["id"], qty=3)
    assert EventRepository(stores).get(ev["id"])["stock"] == 2
    assert hold["ttl_seconds"] > 0
    with pytest.raises(ConflictError):
        holds.create(user_id=user["id"], event_id=ev["id"], qty=3)
    assert EventRepository(stores).get(ev["id"])["stock"] == 2


def test_hold_requires_existing_user_and_event(stores):
    ev = _event(stores)
    with pytest.raises(NotFoundError):
        HoldRepository(stores).create(user_id="999", event_id=ev["id"], qty=1)
    user = _user(stores)
    with pytest.raises(NotFoundError):
        HoldRepository(stores).create(user_id=user["id"], event_id="999", qty=1)


def test_cancel_hold_restores_stock(stores):
    user, ev = _user(stores), _event(stores, capacity=5)
    holds = HoldRepository(stores)
    hold = holds.create(user_id=user["id"], event_id=ev["id"], qty=2)
    assert holds.cancel(hold["id"])["released"] is True
    assert EventRepository(stores).get(ev["id"])["stock"] == 5
    assert stores.redis.client.zcard(keys.holds_expiring()) == 0


def test_cannot_cancel_someone_elses_hold(stores):
    ana, luis = _user(stores), _user(stores, email="luis@example.com")
    ev = _event(stores)
    hold = HoldRepository(stores).create(user_id=ana["id"], event_id=ev["id"], qty=1)
    with pytest.raises(ForbiddenError):
        HoldRepository(stores).cancel(hold["id"], user_id=luis["id"])


def test_expired_hold_is_reclaimed(stores):
    user, ev = _user(stores), _event(stores, capacity=5)
    holds = HoldRepository(stores)
    hold = holds.create(user_id=user["id"], event_id=ev["id"], qty=4)
    # Simular el paso del tiempo: Redis expira la clave y el score queda en el pasado.
    stores.redis.client.delete(keys.hold(hold["id"]))
    member = keys.hold_member(hold["id"], ev["id"], user["id"], 4)
    stores.redis.client.zadd(keys.holds_expiring(), {member: 1})
    assert holds.reclaim_expired() == 1
    assert EventRepository(stores).get(ev["id"])["stock"] == 5
    assert holds.reclaim_expired() == 0  # no se devuelve dos veces


# ---------------------------------------------------------------------- órdenes
def test_confirm_creates_order_in_postgres_and_updates_redis(stores):
    user, ev = _user(stores), _event(stores, capacity=5, price=10.0)
    hold = HoldRepository(stores).create(user_id=user["id"], event_id=ev["id"], qty=2)
    order = OrderRepository(stores).confirm(hold["id"], user_id=user["id"])
    assert order["total"] == 20.0 and order["status"] == "CONFIRMED"
    with pytest.raises(NotFoundError):
        HoldRepository(stores).get(hold["id"])
    assert EventRepository(stores).get(ev["id"])["sold"] == 2                  # PostgreSQL
    assert AnalyticsRepository(stores).top_events()[0] == {"event_id": ev["id"], "name": "Show", "sold": 2}
    assert OrderRepository(stores).list_for_user(user["id"])[0]["id"] == order["id"]
    assert AnalyticsRepository(stores).recent_activity()[0]["order_id"] == order["id"]
    assert AnalyticsRepository(stores).consistency()["ok"]


def test_confirm_twice_is_rejected(stores):
    user, ev = _user(stores), _event(stores)
    hold = HoldRepository(stores).create(user_id=user["id"], event_id=ev["id"], qty=1)
    OrderRepository(stores).confirm(hold["id"])
    with pytest.raises(NotFoundError):
        OrderRepository(stores).confirm(hold["id"])
    assert len(OrderRepository(stores).list_all()) == 1


def test_confirm_someone_elses_hold_forbidden(stores):
    ana, luis = _user(stores), _user(stores, email="luis@example.com")
    ev = _event(stores)
    hold = HoldRepository(stores).create(user_id=ana["id"], event_id=ev["id"], qty=1)
    with pytest.raises(ForbiddenError):
        OrderRepository(stores).confirm(hold["id"], user_id=luis["id"])
    assert HoldRepository(stores).get(hold["id"])["qty"] == 1   # la reserva sigue viva


def test_failed_postgres_transaction_compensates_redis_stock(stores):
    """Saga: si la transacción de PostgreSQL falla, el stock vuelve a Redis."""
    user, ev = _user(stores), _event(stores, capacity=5)
    hold = HoldRepository(stores).create(user_id=user["id"], event_id=ev["id"], qty=2)
    stores.pg.run(lambda cur: cur.execute("DELETE FROM users WHERE id = %s", (int(user["id"]),)))
    with pytest.raises(NotFoundError):
        OrderRepository(stores).confirm(hold["id"])
    assert int(stores.redis.client.get(keys.event_stock(ev["id"]))) == 5   # compensado
    assert EventRepository(stores).get(ev["id"])["sold"] == 0             # sin efecto en PostgreSQL


def test_cancel_order_restores_everything(stores):
    user, ev = _user(stores), _event(stores, capacity=5)
    hold = HoldRepository(stores).create(user_id=user["id"], event_id=ev["id"], qty=2)
    order = OrderRepository(stores).confirm(hold["id"])
    cancelled = OrderRepository(stores).cancel(order["id"], user_id=user["id"])
    assert cancelled["status"] == "CANCELLED"
    got = EventRepository(stores).get(ev["id"])
    assert got["stock"] == 5 and got["sold"] == 0
    assert AnalyticsRepository(stores).top_events() == []
    with pytest.raises(ConflictError):
        OrderRepository(stores).cancel(order["id"])
    assert AnalyticsRepository(stores).consistency()["ok"]


def test_cannot_cancel_someone_elses_order(stores):
    ana, luis = _user(stores), _user(stores, email="luis@example.com")
    ev = _event(stores)
    hold = HoldRepository(stores).create(user_id=ana["id"], event_id=ev["id"], qty=1)
    order = OrderRepository(stores).confirm(hold["id"])
    with pytest.raises(ForbiddenError):
        OrderRepository(stores).cancel(order["id"], user_id=luis["id"])


def test_events_and_users_with_orders_cannot_be_deleted(stores):
    user, ev = _user(stores), _event(stores)
    hold = HoldRepository(stores).create(user_id=user["id"], event_id=ev["id"], qty=1)
    OrderRepository(stores).confirm(hold["id"])
    with pytest.raises(ConflictError):
        EventRepository(stores).delete(ev["id"])
    with pytest.raises(ConflictError):
        UserRepository(stores).delete(user["id"])


def test_audit_log_written_by_triggers(stores):
    user, ev = _user(stores), _event(stores, price=10)
    hold = HoldRepository(stores).create(user_id=user["id"], event_id=ev["id"], qty=1)
    order = OrderRepository(stores).confirm(hold["id"])
    OrderRepository(stores).cancel(order["id"])
    EventRepository(stores).update(ev["id"], price=12.5)
    actions = [(a["action"], a["entity"]) for a in AnalyticsRepository(stores).audit_log()]
    assert {("CREATE", "order"), ("CANCEL", "order"), ("PRICE", "event")} <= set(actions)


# -------------------------------------------------------- reconciliación de datos
def test_reconcile_rebuilds_redis_from_postgres(stores):
    user, ev = _user(stores), _event(stores, capacity=10)
    holds = HoldRepository(stores)
    OrderRepository(stores).confirm(holds.create(user_id=user["id"], event_id=ev["id"], qty=3)["id"])
    holds.create(user_id=user["id"], event_id=ev["id"], qty=2)        # reserva activa
    assert AnalyticsRepository(stores).consistency()["ok"]
    stores.redis.client.delete(keys.event_stock(ev["id"]), keys.ranking_sales())  # "Redis pierde datos"
    assert not AnalyticsRepository(stores).consistency()["ok"]
    report = MaintenanceService(stores).reconcile(fix=True)
    assert report["fixed"] >= 1 and report["now_consistent"]
    assert EventRepository(stores).get(ev["id"])["stock"] == 5        # 10 - 3 vendidas - 2 reservadas
