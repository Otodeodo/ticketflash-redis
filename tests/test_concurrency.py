"""Pruebas de concurrencia: muchos hilos compitiendo por las mismas entradas."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import psycopg

from app import keys
from app.repositories import (AnalyticsRepository, EventRepository, HoldRepository,
                              OrderRepository, UserRepository)
from app.repositories.common import ConflictError, NotFoundError


def _setup(stores, capacity, n_users):
    event = EventRepository(stores).create(name="Flash", venue="Arena", date="2026-12-01T20:00",
                                           category="musica", price=10.0, capacity=capacity)
    users = [UserRepository(stores).create(name=f"U{i}", email=f"u{i}@example.com",
                                           password="clave-segura-123") for i in range(n_users)]
    return event, users


def test_redis_holds_never_oversell(stores):
    """40 compradores simultáneos, 10 entradas: exactamente 10 reservas triunfan."""
    event, users = _setup(stores, capacity=10, n_users=40)

    def try_hold(user):
        try:
            return HoldRepository(stores).create(user_id=user["id"], event_id=event["id"], qty=1)
        except ConflictError:
            return None
    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(try_hold, users))
    assert sum(r is not None for r in results) == 10
    assert int(stores.redis.client.get(keys.event_stock(event["id"]))) == 0


def test_full_purchase_flow_under_concurrency(stores):
    """Reservar y confirmar en paralelo: PostgreSQL termina con sold == capacity, sin excedentes."""
    event, users = _setup(stores, capacity=8, n_users=30)

    def buy(user):
        try:
            hold = HoldRepository(stores).create(user_id=user["id"], event_id=event["id"], qty=1)
            return OrderRepository(stores).confirm(hold["id"], user_id=user["id"])
        except (ConflictError, NotFoundError):
            return None
    with ThreadPoolExecutor(max_workers=15) as pool:
        orders = [o for o in pool.map(buy, users) if o]
    assert len(orders) == 8
    got = EventRepository(stores).get(event["id"])
    assert got["sold"] == 8 and got["stock"] == 0
    report = AnalyticsRepository(stores).consistency()
    assert report["ok"], report


def test_postgres_conditional_update_is_atomic(stores, pg_urls):
    """Aun sin Redis, el UPDATE condicional + CHECK impiden la sobreventa en PostgreSQL."""
    event, _ = _setup(stores, capacity=5, n_users=1)
    eid = int(event["id"])

    def grab(_):
        with psycopg.connect(pg_urls["app"]) as conn:
            return conn.execute(
                "UPDATE ticketflash.events SET sold = sold + 1 "
                "WHERE id = %s AND sold + 1 <= capacity", (eid,)).rowcount
    with ThreadPoolExecutor(max_workers=20) as pool:
        won = sum(pool.map(grab, range(30)))
    assert won == 5
    assert stores.pg.fetch_one("SELECT sold FROM events WHERE id = %s", (eid,))["sold"] == 5


def test_check_constraint_blocks_oversell_even_with_naive_update(stores):
    """Si una app defectuosa se saltara la condición, el CHECK rechaza la fila."""
    event, _ = _setup(stores, capacity=2, n_users=1)
    try:
        stores.pg.run(lambda cur: cur.execute(
            "UPDATE events SET sold = sold + 3 WHERE id = %s", (int(event["id"]),)))
    except psycopg.errors.CheckViolation:
        pass
    else:
        raise AssertionError("El CHECK ck_events_sold debía impedir sold > capacity")


def test_double_confirm_same_hold_creates_single_order(stores):
    """Dos confirmaciones simultáneas de la MISMA reserva: solo una orden."""
    event, users = _setup(stores, capacity=5, n_users=1)
    hold = HoldRepository(stores).create(user_id=users[0]["id"], event_id=event["id"], qty=2)

    def confirm(_):
        try:
            return OrderRepository(stores).confirm(hold["id"])
        except (ConflictError, NotFoundError):
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        orders = [o for o in pool.map(confirm, range(8)) if o]
    assert len(orders) == 1
    assert EventRepository(stores).get(event["id"])["sold"] == 2


def test_row_lock_serializes_concurrent_cancellations(stores):
    """FOR UPDATE: cancelar la misma orden desde 6 hilos solo restituye UNA vez."""
    event, users = _setup(stores, capacity=5, n_users=1)
    hold = HoldRepository(stores).create(user_id=users[0]["id"], event_id=event["id"], qty=3)
    order = OrderRepository(stores).confirm(hold["id"])

    def cancel(_):
        try:
            OrderRepository(stores).cancel(order["id"])
            return True
        except ConflictError:
            return False
    with ThreadPoolExecutor(max_workers=6) as pool:
        wins = sum(pool.map(cancel, range(6)))
    assert wins == 1
    got = EventRepository(stores).get(event["id"])
    assert got["sold"] == 0 and got["stock"] == 5
