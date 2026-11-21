"""Repositorio de eventos: datos en PostgreSQL + estado en caliente en Redis.

  PostgreSQL (fuente de verdad)         Redis (rápido, derivado)
  -----------------------------         ------------------------
  events / venues / categories          tf:event:<id>:stock     STRING  stock disponible
  (restricciones, versión, auditoría)   tf:event:<id>:visitors  HLL     visitantes únicos
                                        tf:cache:event:<id>:summary  caché con TTL

Invariante que mantiene todo el sistema:
    stock_redis = capacity - sold - (entradas en reservas activas)
"""

from __future__ import annotations

import json
from datetime import datetime

from psycopg import errors as pgerr

from app import keys
from app.config import settings
from app.repositories.common import ConflictError, NotFoundError, to_int_id
from app.stores import Stores

_CATALOG = ("SELECT id, name, venue, category, starts_at, price, capacity, sold, version "
            "FROM v_event_catalog")


def _catalog_id(cur, table: str, name: str) -> int:
    """Obtiene el id de una categoría/recinto, creándolo si no existe.

    ON CONFLICT DO NOTHING + SELECT es seguro ante inserciones concurrentes y solo
    requiere los privilegios SELECT/INSERT que tiene el rol de la aplicación."""
    cur.execute(f"INSERT INTO {table} (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (name,))
    return cur.execute(f"SELECT id FROM {table} WHERE name = %s", (name,)).fetchone()["id"]


def _parse_date(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ConflictError("Fecha inválida: usa el formato 2026-12-05T20:00") from exc


class EventRepository:
    def __init__(self, stores: Stores):
        self.pg = stores.pg
        self.r = stores.redis.client

    # --- Create -----------------------------------------------------------------
    def create(self, *, name: str, venue: str, date: str, category: str,
               price: float, capacity: int) -> dict:
        starts_at = _parse_date(date)

        def tx(cur):
            category_id = _catalog_id(cur, "categories", category.strip().lower())
            venue_id = _catalog_id(cur, "venues", venue.strip())
            return cur.execute(
                "INSERT INTO events (name, venue_id, category_id, starts_at, price, capacity) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (name.strip(), venue_id, category_id, starts_at, price, capacity)).fetchone()["id"]
        event_id = str(self.pg.run(tx))
        self.r.set(keys.event_stock(event_id), capacity, nx=True)   # stock en caliente
        return self.get(event_id)

    # --- Read -------------------------------------------------------------------
    def get(self, event_id: str, *, track_visitor: str | None = None) -> dict:
        row = self.pg.fetch_one(f"{_CATALOG} WHERE id = %s", (to_int_id(event_id, "Evento"),))
        if not row:
            raise NotFoundError(f"Evento {event_id} no existe")
        if track_visitor:
            # HyperLogLog: cuenta visitantes únicos con ~12 KB por evento.
            self.r.pfadd(keys.event_visitors(event_id), track_visitor)
        return self._many([row])[0]

    def list_all(self) -> list[dict]:
        return self._many(self.pg.fetch_all(f"{_CATALOG} ORDER BY starts_at, id"))

    def list_by_category(self, category: str) -> list[dict]:
        # Usa el índice (category_id, starts_at) a través de la vista.
        return self._many(self.pg.fetch_all(
            f"{_CATALOG} WHERE category = %s ORDER BY starts_at, id", (category.strip().lower(),)))

    def categories(self) -> list[str]:
        rows = self.pg.fetch_all(
            "SELECT c.name FROM categories c "
            "WHERE EXISTS (SELECT 1 FROM events e WHERE e.category_id = c.id) ORDER BY c.name")
        return [r["name"] for r in rows]

    def summary_cached(self, event_id: str) -> tuple[dict, bool]:
        """Patrón cache-aside (Redis): devuelve (resumen, hit_de_cache)."""
        cache_key = keys.event_summary_cache(event_id)
        cached = self.r.get(cache_key)
        if cached:
            return json.loads(cached), True
        event = self.get(event_id)
        summary = {"id": event["id"], "name": event["name"], "stock": event["stock"],
                   "sold": event["sold"], "unique_visitors": event["unique_visitors"]}
        self.r.set(cache_key, json.dumps(summary), ex=settings.cache_ttl)
        return summary, False

    def ensure_stock(self, event_id: str) -> None:
        """Garantiza que exista el contador de stock en Redis (lo reconstruye desde
        PostgreSQL si Redis lo perdió) y que el evento exista."""
        if self.r.exists(keys.event_stock(event_id)):
            return
        row = self.pg.fetch_one("SELECT capacity, sold FROM events WHERE id = %s",
                                (to_int_id(event_id, "Evento"),))
        if not row:
            raise NotFoundError(f"Evento {event_id} no existe")
        self.r.set(keys.event_stock(event_id), row["capacity"] - row["sold"], nx=True)

    # --- Update -----------------------------------------------------------------
    def update(self, event_id: str, *, version: int | None = None, **fields) -> dict:
        """Actualiza campos del evento.

        Si se envía `version`, se aplica BLOQUEO OPTIMISTA: la actualización solo
        ocurre si nadie más modificó el evento desde que el cliente lo leyó."""
        eid = to_int_id(event_id, "Evento")
        clean = {k: v for k, v in fields.items() if v is not None}
        if not clean:
            return self.get(event_id)
        starts_at = _parse_date(clean["date"]) if "date" in clean else None

        def tx(cur):
            sets, params = [], []
            if "name" in clean:
                sets.append("name = %s"); params.append(clean["name"].strip())
            if "venue" in clean:
                sets.append("venue_id = %s"); params.append(_catalog_id(cur, "venues", clean["venue"].strip()))
            if "category" in clean:
                sets.append("category_id = %s")
                params.append(_catalog_id(cur, "categories", clean["category"].strip().lower()))
            if starts_at is not None:
                sets.append("starts_at = %s"); params.append(starts_at)
            if "price" in clean:
                sets.append("price = %s"); params.append(clean["price"])
            sets.append("version = version + 1")
            where = "id = %s"
            params.append(eid)
            if version is not None:
                where += " AND version = %s"; params.append(version)
            row = cur.execute(f"UPDATE events SET {', '.join(sets)} WHERE {where} RETURNING id",
                              params).fetchone()
            if row:
                return True
            exists = cur.execute("SELECT 1 AS x FROM events WHERE id = %s", (eid,)).fetchone()
            if not exists:
                raise NotFoundError(f"Evento {event_id} no existe")
            return False
        if not self.pg.run(tx):
            raise ConflictError("El evento fue modificado por otro usuario; recarga e inténtalo de nuevo")
        self.r.delete(keys.event_summary_cache(event_id))  # invalidar caché
        return self.get(event_id)

    def add_stock(self, event_id: str, qty: int) -> int:
        """Cambia la capacidad (y el stock) de forma atómica en ambas bases.

        1) Redis INCRBY (atómico) -> si el stock quedara negativo, se revierte.
        2) PostgreSQL UPDATE de capacity (el CHECK impide capacity < sold).
        Si PostgreSQL falla, se compensa el cambio en Redis."""
        eid = to_int_id(event_id, "Evento")
        self.ensure_stock(event_id)
        new_stock = self.r.incrby(keys.event_stock(event_id), qty)
        if new_stock < 0:
            self.r.incrby(keys.event_stock(event_id), -qty)
            raise ConflictError("El stock no puede quedar negativo")
        try:
            row = self.pg.run(lambda cur: cur.execute(
                "UPDATE events SET capacity = capacity + %s, version = version + 1 "
                "WHERE id = %s RETURNING id", (qty, eid)).fetchone())
        except pgerr.CheckViolation as exc:
            self.r.incrby(keys.event_stock(event_id), -qty)
            raise ConflictError("La capacidad no puede ser menor que las entradas ya vendidas") from exc
        if not row:
            self.r.incrby(keys.event_stock(event_id), -qty)
            raise NotFoundError(f"Evento {event_id} no existe")
        self.r.delete(keys.event_summary_cache(event_id))
        return int(new_stock)

    # --- Delete -----------------------------------------------------------------
    def delete(self, event_id: str) -> None:
        eid = to_int_id(event_id, "Evento")
        try:
            deleted = self.pg.run(lambda cur: cur.execute(
                "DELETE FROM events WHERE id = %s", (eid,)).rowcount)
        except pgerr.ForeignKeyViolation as exc:
            raise ConflictError("El evento tiene órdenes registradas y no puede eliminarse") from exc
        if not deleted:
            raise NotFoundError(f"Evento {event_id} no existe")
        pipe = self.r.pipeline(transaction=True)
        pipe.delete(keys.event_stock(event_id), keys.event_visitors(event_id),
                    keys.event_summary_cache(event_id))
        pipe.zrem(keys.ranking_sales(), event_id)
        pipe.execute()

    # --- Helpers ----------------------------------------------------------------
    def _many(self, rows: list[dict]) -> list[dict]:
        """Combina las filas de PostgreSQL con stock y visitantes de Redis (1 viaje)."""
        if not rows:
            return []
        pipe = self.r.pipeline(transaction=False)
        for row in rows:
            eid = str(row["id"])
            pipe.get(keys.event_stock(eid))
            pipe.pfcount(keys.event_visitors(eid))
        results = pipe.execute()
        events = []
        for i, row in enumerate(rows):
            stock, visitors = results[2 * i], results[2 * i + 1]
            if stock is None:   # Redis perdió el contador: se reconstruye desde PostgreSQL
                stock = row["capacity"] - row["sold"]
                self.r.set(keys.event_stock(str(row["id"])), stock, nx=True)
            events.append({
                "id": str(row["id"]), "name": row["name"], "venue": row["venue"],
                "category": row["category"],
                "date": row["starts_at"].isoformat(timespec="minutes"),
                "price": float(row["price"]), "capacity": row["capacity"],
                "stock": int(stock), "sold": row["sold"], "version": row["version"],
                "unique_visitors": int(visitors or 0),
            })
        return events
