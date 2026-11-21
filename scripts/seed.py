"""Script de datos iniciales (seeding) para AMBAS bases de datos.

Uso:
    python scripts/seed.py            # puebla (falla si ya hay datos)
    python scripts/seed.py --reset    # borra datos de la app y repuebla
    python scripts/seed.py --bulk 20000   # añade 20 000 órdenes históricas (prueba de índices)

Crea 1 administrador y 5 clientes (PostgreSQL), 7 eventos (PostgreSQL + stock en
Redis), 7 órdenes confirmadas (saga completa) y una reserva temporal activa.

Contraseñas de demostración: variable SEED_PASSWORD (por defecto la indicada abajo).
CÁMBIALAS si la base es accesible desde Internet.

--reset necesita permisos de borrado total: usa --admin-url (dueño de la base) o la
variable ADMIN_DATABASE_URL; el usuario de la aplicación (tf_app) NO puede borrar órdenes.
"""

from __future__ import annotations

import argparse
import os
import sys

import psycopg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import keys  # noqa: E402
from app.repositories import (EventRepository, HoldRepository, OrderRepository,  # noqa: E402
                              UserRepository)
from app.stores import get_stores  # noqa: E402

PASSWORD = os.getenv("SEED_PASSWORD", "TicketFlash2026!")
ADMIN = ("Administrador", "admin@example.com")
USERS = [
    ("Ana Torres", "ana@example.com"),
    ("Luis Gómez", "luis@example.com"),
    ("María Pérez", "maria@example.com"),
    ("Carlos Ruiz", "carlos@example.com"),
    ("Sofía Herrera", "sofia@example.com"),
]

EVENTS = [
    # nombre, recinto, fecha, categoría, precio, capacidad
    ("Concierto Rock Nacional", "Estadio Central", "2026-12-05T20:00", "musica", 45.0, 120),
    ("Festival Jazz de Invierno", "Teatro Municipal", "2026-12-12T19:30", "musica", 30.0, 80),
    ("Final Liga de Baloncesto", "Arena Norte", "2026-12-20T18:00", "deporte", 25.0, 150),
    ("Maratón Urbano 10K", "Parque Metropolitano", "2027-01-10T07:00", "deporte", 15.0, 300),
    ("Obra: La Casa de Bernarda Alba", "Teatro Clásico", "2026-12-28T20:30", "teatro", 20.0, 60),
    ("Stand-up Comedy Night", "Club Risa", "2026-12-30T21:00", "comedia", 12.5, 40),
    ("Conferencia Tech de Bases de Datos", "Centro de Convenciones", "2026-12-15T09:00", "tecnologia", 50.0, 200),
]

# (índice usuario, índice evento, cantidad) -> órdenes confirmadas iniciales
ORDERS = [(0, 0, 2), (1, 0, 3), (2, 2, 4), (3, 2, 2), (4, 6, 1), (0, 1, 2), (1, 2, 1)]


def wipe(stores, admin_url: str | None) -> None:
    """Vacía PostgreSQL (con el rol dueño) y las claves del namespace en Redis (SCAN, nunca FLUSHDB)."""
    if not admin_url:
        sys.exit("--reset necesita --admin-url (o ADMIN_DATABASE_URL): tf_app no puede borrar órdenes.")
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute("TRUNCATE ticketflash.orders, ticketflash.events, ticketflash.users, "
                     "ticketflash.venues, ticketflash.categories RESTART IDENTITY CASCADE")
    deleted, batch = 0, []
    for key in stores.redis.client.scan_iter(match=keys.all_pattern(), count=500):
        batch.append(key)
        if len(batch) >= 500:
            deleted += stores.redis.client.delete(*batch); batch = []
    if batch:
        deleted += stores.redis.client.delete(*batch)
    print(f"Bases limpiadas (claves de Redis eliminadas: {deleted})")


def bulk_orders(admin_url: str | None, n: int) -> None:
    """Inserta N órdenes históricas en PostgreSQL con una sola sentencia (generate_series)."""
    if not admin_url:
        sys.exit("--bulk necesita --admin-url (o ADMIN_DATABASE_URL)")
    with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
        cur.execute("SET search_path = ticketflash")
        cur.execute("ALTER TABLE events DROP CONSTRAINT ck_events_sold")   # se restaura abajo
        cur.execute(
            "INSERT INTO orders (user_id, event_id, qty, unit_price, created_at) "
            "SELECT u.id, e.id, 1 + floor(random() * 4)::int, e.price, now() - random() * interval '90 days' "
            "FROM generate_series(1, %s) g "
            "CROSS JOIN LATERAL (SELECT id, price FROM events WHERE g.g > 0 ORDER BY random() LIMIT 1) e "
            "CROSS JOIN LATERAL (SELECT id FROM users WHERE g.g > 0 AND role = 'customer' "
            "                    ORDER BY random() LIMIT 1) u", (n,))
        cur.execute("UPDATE events e SET sold = s.q FROM (SELECT event_id, SUM(qty) AS q FROM orders "
                    "WHERE status = 'CONFIRMED' GROUP BY event_id) s WHERE s.event_id = e.id")
        cur.execute("UPDATE events SET capacity = GREATEST(capacity, sold)")
        cur.execute("ALTER TABLE events ADD CONSTRAINT ck_events_sold CHECK (sold >= 0 AND sold <= capacity)")
        cur.execute("ANALYZE orders")
    print(f"{n} órdenes históricas insertadas. Ejecuta: python -m app.cli reconcile")


def seed(reset: bool, admin_url: str | None, bulk: int) -> None:
    stores = get_stores()
    health = stores.health()
    print(f"Conectado a PostgreSQL {health.get('pg_version')} (usuario {health.get('pg_user')}) "
          f"y Redis {health.get('redis_version')}")
    if reset:
        wipe(stores, admin_url)
    elif stores.pg.fetch_one("SELECT 1 AS x FROM events LIMIT 1"):
        print("La base ya contiene datos. Usa --reset para repoblar.")
        return

    users, events = UserRepository(stores), EventRepository(stores)
    holds, orders = HoldRepository(stores), OrderRepository(stores)

    users.create(name=ADMIN[0], email=ADMIN[1], password=PASSWORD, role="admin")
    user_ids = [users.create(name=n, email=e, password=PASSWORD)["id"] for n, e in USERS]
    print(f"Usuarios creados: 1 administrador + {len(user_ids)} clientes")

    event_ids = [events.create(name=n, venue=v, date=d, category=c, price=p, capacity=cap)["id"]
                 for n, v, d, c, p, cap in EVENTS]
    print(f"Eventos creados: {len(event_ids)}")

    for ui, ei, qty in ORDERS:
        hold = holds.create(user_id=user_ids[ui], event_id=event_ids[ei], qty=qty)
        orders.confirm(hold["id"])
    print(f"Órdenes confirmadas: {len(ORDERS)}")

    hold = holds.create(user_id=user_ids[2], event_id=event_ids[1], qty=2)
    print(f"Reserva temporal activa: id={hold['id']} expira en {hold['ttl_seconds']} s")
    if bulk:
        bulk_orders(admin_url, bulk)
    print(f"Listo. Administrador: {ADMIN[1]}  ·  clientes: ana@example.com, luis@example.com, ...")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true", help="Borrar y repoblar")
    parser.add_argument("--admin-url", default=os.getenv("ADMIN_DATABASE_URL"))
    parser.add_argument("--bulk", type=int, default=0, help="Órdenes históricas adicionales")
    a = parser.parse_args()
    seed(a.reset, a.admin_url, a.bulk)
