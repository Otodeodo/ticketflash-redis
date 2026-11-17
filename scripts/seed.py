"""Script de datos iniciales (seeding).

Uso:
    python scripts/seed.py            # puebla la base (falla si ya hay datos)
    python scripts/seed.py --reset    # borra todas las claves del namespace y repuebla

Crea usuarios, eventos con stock, algunas órdenes confirmadas (para que el
ranking tenga datos) y una reserva temporal activa.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import keys  # noqa: E402
from app.db import get_db  # noqa: E402
from app.repositories import (EventRepository, HoldRepository, OrderRepository,  # noqa: E402
                              UserRepository)

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
    ("Obra: La Casa de Bernarda Alba", "Teatro Clásico", "2026-11-28T20:30", "teatro", 20.0, 60),
    ("Stand-up Comedy Night", "Club Risa", "2026-11-30T21:00", "comedia", 12.5, 40),
    ("Conferencia Tech NoSQL", "Centro de Convenciones", "2026-12-15T09:00", "tecnologia", 50.0, 200),
]

# (índice usuario, índice evento, cantidad) -> órdenes confirmadas iniciales
ORDERS = [(0, 0, 2), (1, 0, 3), (2, 2, 4), (3, 2, 2), (4, 6, 1), (0, 1, 2), (1, 2, 1)]


def wipe(db) -> int:
    """Elimina solo las claves del namespace de la app usando SCAN (no FLUSHDB)."""
    deleted = 0
    batch = []
    for key in db.client.scan_iter(match=keys.all_pattern(), count=500):
        batch.append(key)
        if len(batch) >= 500:
            deleted += db.client.delete(*batch)
            batch = []
    if batch:
        deleted += db.client.delete(*batch)
    return deleted


def seed(reset: bool) -> None:
    db = get_db()
    print("Conectado a Redis:", db.info_summary())
    if reset:
        print("Claves eliminadas:", wipe(db))
    elif db.client.exists(keys.events_index()):
        print("La base ya contiene datos. Usa --reset para repoblar.")
        return

    users, events = UserRepository(db), EventRepository(db)
    holds, orders = HoldRepository(db), OrderRepository(db)

    user_ids = [users.create(name=n, email=e)["id"] for n, e in USERS]
    print(f"Usuarios creados: {len(user_ids)}")

    event_ids = []
    for name, venue, date, cat, price, cap in EVENTS:
        event_ids.append(events.create(name=name, venue=venue, date=date, category=cat,
                                       price=price, capacity=cap)["id"])
    print(f"Eventos creados: {len(event_ids)}")

    for ui, ei, qty in ORDERS:
        hold = holds.create(user_id=user_ids[ui], event_id=event_ids[ei], qty=qty)
        orders.confirm(hold["id"])
    print(f"Órdenes confirmadas: {len(ORDERS)}")

    hold = holds.create(user_id=user_ids[2], event_id=event_ids[1], qty=2)
    print(f"Reserva temporal activa: id={hold['id']} expira en {hold['ttl_seconds']} s")
    print("Listo.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="Borrar y repoblar")
    seed(parser.parse_args().reset)
