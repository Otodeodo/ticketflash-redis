"""Demostración de CONCURRENCIA sobre las dos bases de datos (para el video).

    python scripts/concurrency_demo.py                 # 60 compradores, 20 entradas
    python scripts/concurrency_demo.py --buyers 100 --capacity 30

Muestra tres mecanismos:
  1. Compra masiva simultánea: Lua atómico en Redis + UPDATE condicional en PostgreSQL
     -> nunca se vende más de lo que hay y ambas bases terminan coincidiendo.
  2. Bloqueo de fila en PostgreSQL: una transacción hace esperar a la otra (sin perder datos).
  3. Bloqueo optimista: dos administradores editan el mismo evento; el segundo recibe conflicto.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.repositories import (AnalyticsRepository, EventRepository, HoldRepository,  # noqa: E402
                              OrderRepository, UserRepository)
from app.repositories.common import ConflictError, NotFoundError  # noqa: E402
from app.stores import get_stores  # noqa: E402


def part1(stores, buyers: int, capacity: int) -> None:
    print(f"\n=== 1) {buyers} compradores simultáneos, {capacity} entradas ===")
    events, users = EventRepository(stores), UserRepository(stores)
    event = events.create(name="Demo concurrencia", venue="Sala Demo", date="2027-01-15T21:00",
                          category="demo", price=10.0, capacity=capacity)
    existing = {u["email"]: u for u in users.list_all()}
    buyer_users = []
    for i in range(buyers):
        email = f"demo{i}@example.com"
        buyer_users.append(existing.get(email) or users.create(name=f"Demo {i}", email=email,
                                                               password="clave-demo-123"))
    start = threading.Barrier(buyers)
    outcome = {"comprados": 0, "sin_stock": 0, "otros": 0}
    lock = threading.Lock()

    def buy(user):
        start.wait()                       # todos disparan a la vez
        try:
            hold = HoldRepository(stores).create(user_id=user["id"], event_id=event["id"], qty=1)
            OrderRepository(stores).confirm(hold["id"], user_id=user["id"])
            key = "comprados"
        except ConflictError:
            key = "sin_stock"
        except NotFoundError:
            key = "otros"
        with lock:
            outcome[key] += 1

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=buyers) as pool:
        list(pool.map(buy, buyer_users))
    elapsed = time.perf_counter() - t0
    final = events.get(event["id"])
    print(f"Resultado: {outcome}  ({elapsed:.2f} s)")
    print(f"PostgreSQL  -> vendidas: {final['sold']} / capacidad {final['capacity']}")
    print(f"Redis       -> stock disponible: {final['stock']}")
    ok = final["sold"] <= capacity and outcome["comprados"] == final["sold"]
    report = AnalyticsRepository(stores).consistency()
    print("Sin sobreventa:", "SI" if ok else "NO", " ·  PostgreSQL ↔ Redis consistentes:",
          "SI" if report["ok"] else "NO")


def part2(stores) -> None:
    print("\n=== 2) Bloqueo de fila en PostgreSQL (la 2.ª transacción espera a la 1.ª) ===")
    event = EventRepository(stores).list_all()[0]
    eid = int(event["id"])
    holding = threading.Event()
    waited = {}

    def t1(cur):
        cur.execute("UPDATE events SET price = price WHERE id = %s", (eid,))   # toma el bloqueo de fila
        holding.set()
        print("T1: tiene la fila bloqueada durante 3 s...")
        time.sleep(3)

    def t2(cur):
        holding.wait()
        print("T2: intenta actualizar la misma fila...")
        began = time.perf_counter()
        cur.execute("UPDATE events SET price = price WHERE id = %s", (eid,))
        waited["s"] = time.perf_counter() - began

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1, f2 = pool.submit(stores.pg.run, t1), pool.submit(stores.pg.run, t2)
        f1.result(); f2.result()
    print(f"T2 esperó {waited['s']:.1f} s y luego continuó sin error: PostgreSQL serializa el acceso a la fila.")


def part3(stores) -> None:
    print("\n=== 3) Bloqueo optimista (columna version) ===")
    events = EventRepository(stores)
    ev = events.list_all()[0]
    print(f"Dos administradores leen '{ev['name']}' en versión {ev['version']}.")
    events.update(ev["id"], version=ev["version"], price=ev["price"] + 1)
    print("Admin A guarda su cambio de precio: OK.")
    try:
        events.update(ev["id"], version=ev["version"], price=ev["price"] + 5)
    except ConflictError as exc:
        print(f"Admin B intenta guardar con la versión vieja: RECHAZADO -> {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--buyers", type=int, default=60)
    parser.add_argument("--capacity", type=int, default=20)
    args = parser.parse_args()
    stores = get_stores()
    h = stores.health()
    print(f"PostgreSQL {h.get('pg_version')} ({h.get('pg_user')})  ·  Redis {h.get('redis_version')}")
    part1(stores, args.buyers, args.capacity)
    part2(stores)
    part3(stores)


if __name__ == "__main__":
    main()
