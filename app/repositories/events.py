"""Repositorio de eventos.

Estructuras usadas:
  * HASH   tf:event:<id>            -> campos del evento
  * STRING tf:event:<id>:stock      -> entero con las entradas disponibles
  * SET    tf:events                -> índice principal
  * SET    tf:events:category:<c>   -> índice secundario
  * HLL    tf:event:<id>:visitors   -> visitantes únicos aproximados
  * STRING tf:cache:event:<id>:summary (TTL corto) -> resumen JSON cacheado
"""

from __future__ import annotations

import json

from app import keys
from app.config import settings
from app.db import Database
from app.repositories.common import ConflictError, NotFoundError, next_id


class EventRepository:
    def __init__(self, db: Database):
        self.db = db
        self.r = db.client

    # --- Create -----------------------------------------------------------
    def create(self, *, name: str, venue: str, date: str, category: str,
               price: float, capacity: int) -> dict:
        event_id = next_id(self.db, "event")
        data = {
            "id": event_id, "name": name, "venue": venue, "date": date,
            "category": category, "price": str(price), "capacity": str(capacity),
        }
        # Pipeline con MULTI/EXEC: todas las escrituras se aplican juntas.
        pipe = self.r.pipeline(transaction=True)
        pipe.hset(keys.event(event_id), mapping=data)
        pipe.set(keys.event_stock(event_id), capacity)
        pipe.sadd(keys.events_index(), event_id)
        pipe.sadd(keys.events_by_category(category), event_id)
        pipe.execute()
        return self.get(event_id)

    # --- Read -------------------------------------------------------------
    def get(self, event_id: str, *, track_visitor: str | None = None) -> dict:
        pipe = self.r.pipeline(transaction=False)
        pipe.hgetall(keys.event(event_id))
        pipe.get(keys.event_stock(event_id))
        pipe.zscore(keys.ranking_sales(), event_id)
        pipe.pfcount(keys.event_visitors(event_id))
        raw, stock, sold, visitors = pipe.execute()
        if not raw:
            raise NotFoundError(f"Evento {event_id} no existe")
        if track_visitor:
            # HyperLogLog: cuenta visitantes únicos con ~12 KB por evento.
            self.r.pfadd(keys.event_visitors(event_id), track_visitor)
        return self._hydrate(raw, stock, sold, visitors)

    def list_all(self) -> list[dict]:
        ids = sorted(self.r.smembers(keys.events_index()), key=int)
        return self._many(ids)

    def list_by_category(self, category: str) -> list[dict]:
        ids = sorted(self.r.smembers(keys.events_by_category(category)), key=int)
        return self._many(ids)

    def categories(self) -> list[str]:
        cats = set()
        for key in self.r.scan_iter(match=f"{keys.P}:events:category:*", count=100):
            cats.add(key.rsplit(":", 1)[-1])
        return sorted(cats)

    def summary_cached(self, event_id: str) -> tuple[dict, bool]:
        """Patrón cache-aside: devuelve (resumen, hit_de_cache)."""
        cache_key = keys.event_summary_cache(event_id)
        cached = self.r.get(cache_key)
        if cached:
            return json.loads(cached), True
        event = self.get(event_id)
        summary = {
            "id": event["id"], "name": event["name"], "stock": event["stock"],
            "sold": event["sold"], "unique_visitors": event["unique_visitors"],
        }
        self.r.set(cache_key, json.dumps(summary), ex=settings.cache_ttl)
        return summary, False

    # --- Update -----------------------------------------------------------
    def update(self, event_id: str, **fields) -> dict:
        key = keys.event(event_id)
        if not self.r.exists(key):
            raise NotFoundError(f"Evento {event_id} no existe")
        clean = {k: str(v) for k, v in fields.items() if v is not None}
        if not clean:
            return self.get(event_id)
        pipe = self.r.pipeline(transaction=True)
        if "category" in clean:
            old = self.r.hget(key, "category")
            if old and old != clean["category"]:
                pipe.srem(keys.events_by_category(old), event_id)
                pipe.sadd(keys.events_by_category(clean["category"]), event_id)
        pipe.hset(key, mapping=clean)
        pipe.delete(keys.event_summary_cache(event_id))  # invalidar caché
        pipe.execute()
        return self.get(event_id)

    def add_stock(self, event_id: str, qty: int) -> int:
        """Incrementa (o decrementa) el stock de forma atómica."""
        if not self.r.exists(keys.event(event_id)):
            raise NotFoundError(f"Evento {event_id} no existe")
        new_stock = self.r.incrby(keys.event_stock(event_id), qty)
        if new_stock < 0:
            self.r.incrby(keys.event_stock(event_id), -qty)
            raise ConflictError("El stock no puede quedar negativo")
        self.r.hincrby(keys.event(event_id), "capacity", qty)
        return new_stock

    # --- Delete -----------------------------------------------------------
    def delete(self, event_id: str) -> None:
        key = keys.event(event_id)
        category = self.r.hget(key, "category")
        if category is None:
            raise NotFoundError(f"Evento {event_id} no existe")
        pipe = self.r.pipeline(transaction=True)
        pipe.delete(key, keys.event_stock(event_id), keys.event_visitors(event_id),
                    keys.event_summary_cache(event_id))
        pipe.srem(keys.events_index(), event_id)
        pipe.srem(keys.events_by_category(category), event_id)
        pipe.zrem(keys.ranking_sales(), event_id)
        pipe.execute()

    # --- Helpers ----------------------------------------------------------
    def _many(self, ids: list[str]) -> list[dict]:
        if not ids:
            return []
        pipe = self.r.pipeline(transaction=False)
        for event_id in ids:
            pipe.hgetall(keys.event(event_id))
            pipe.get(keys.event_stock(event_id))
            pipe.zscore(keys.ranking_sales(), event_id)
            pipe.pfcount(keys.event_visitors(event_id))
        results = pipe.execute()
        events = []
        for i in range(0, len(results), 4):
            raw, stock, sold, visitors = results[i:i + 4]
            if raw:
                events.append(self._hydrate(raw, stock, sold, visitors))
        return events

    @staticmethod
    def _hydrate(raw: dict, stock, sold, visitors) -> dict:
        return {
            **raw,
            "price": float(raw["price"]),
            "capacity": int(raw["capacity"]),
            "stock": int(stock or 0),
            "sold": int(float(sold or 0)),
            "unique_visitors": int(visitors or 0),
        }
