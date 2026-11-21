"""Pruebas del modelo de seguridad de PostgreSQL (roles y privilegios)."""

from __future__ import annotations

import psycopg
import pytest
from psycopg import errors as pgerr

from app.repositories import EventRepository, HoldRepository, OrderRepository, UserRepository


def _seed(stores):
    user = UserRepository(stores).create(name="Ana", email="ana@example.com", password="clave-segura-123")
    event = EventRepository(stores).create(name="Show", venue="Sala", date="2026-12-01T20:00",
                                           category="musica", price=10.0, capacity=5)
    hold = HoldRepository(stores).create(user_id=user["id"], event_id=event["id"], qty=1)
    OrderRepository(stores).confirm(hold["id"])
    return user, event


def _connect(url):
    return psycopg.connect(url, autocommit=True)


def test_app_role_cannot_delete_orders_or_touch_audit(stores, pg_urls):
    _seed(stores)
    with _connect(pg_urls["app"]) as conn:
        for sql in ("DELETE FROM ticketflash.orders",
                    "TRUNCATE ticketflash.orders",
                    "INSERT INTO ticketflash.audit_log (action, entity) VALUES ('X', 'y')",
                    "UPDATE ticketflash.audit_log SET action = 'borrado'",
                    "DELETE FROM ticketflash.audit_log",
                    "UPDATE ticketflash.orders SET qty = 9",          # columna no permitida
                    "UPDATE ticketflash.orders SET unit_price = 0.01",
                    "CREATE TABLE ticketflash.hack (x int)",
                    "CREATE ROLE intruso"):
            with pytest.raises(pgerr.InsufficientPrivilege):
                conn.execute(sql)


def test_app_role_can_read_audit_trail(stores, pg_urls):
    _seed(stores)
    with _connect(pg_urls["app"]) as conn:
        assert conn.execute("SELECT count(*) FROM ticketflash.audit_log").fetchone()[0] >= 1


def test_readonly_role_cannot_write_or_read_password_hash(stores, pg_urls):
    _seed(stores)
    with _connect(pg_urls["analyst"]) as conn:
        assert conn.execute("SELECT count(*) FROM ticketflash.orders").fetchone()[0] == 1
        assert conn.execute("SELECT * FROM ticketflash.v_sales_by_event").fetchone() is not None
        with pytest.raises(pgerr.InsufficientPrivilege):
            conn.execute("SELECT password_hash FROM ticketflash.users")
        with pytest.raises(pgerr.InsufficientPrivilege):
            conn.execute("SELECT * FROM ticketflash.audit_log")
        for sql in ("INSERT INTO ticketflash.categories (name) VALUES ('x')",
                    "UPDATE ticketflash.events SET price = 1",
                    "DELETE FROM ticketflash.events"):
            with pytest.raises(pgerr.InsufficientPrivilege):
                conn.execute(sql)


def test_backup_role_reads_everything_but_writes_nothing(stores, pg_urls):
    _seed(stores)
    with _connect(pg_urls["backup"]) as conn:
        for table in ("users", "events", "orders", "audit_log", "categories", "venues"):
            conn.execute(f"SELECT count(*) FROM ticketflash.{table}").fetchone()
        with pytest.raises(pgerr.InsufficientPrivilege):
            conn.execute("DELETE FROM ticketflash.orders")


def test_wrong_password_is_rejected(pg_urls):
    try:
        with _connect(pg_urls["admin"]) as conn:
            methods = {r[0] for r in conn.execute(
                "SELECT auth_method FROM pg_hba_file_rules WHERE type LIKE 'host%'").fetchall()}
        if "trust" in methods:
            pytest.skip("El servidor de pruebas embebido usa autenticación 'trust' (solo local)")
    except pgerr.InsufficientPrivilege:
        pass
    bad = pg_urls["app"].replace("test-app-pw", "contrasena-incorrecta")
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(bad, connect_timeout=5)


def test_app_role_is_not_superuser(pg_urls):
    with _connect(pg_urls["app"]) as conn:
        assert conn.execute("SELECT rolsuper, rolcreaterole, rolcreatedb FROM pg_roles "
                            "WHERE rolname = current_user").fetchone() == (False, False, False)
