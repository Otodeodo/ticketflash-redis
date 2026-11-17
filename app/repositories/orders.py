"""Repositorio de órdenes confirmadas (HASH sin TTL + LIST por usuario)."""

from __future__ import annotations

import json

from app import keys
from app.config import settings
from app.db import Database
from app.repositories.common import ConflictError, NotFoundError, next_id, now_iso


class OrderRepository:
    def __init__(self, db: Database):
        self.db = db
        self.r = db.client

    def confirm(self, hold_id: str) -> dict:
        """Convierte una reserva vigente en orden. Todo ocurre en un script Lua."""
        hold = self.r.hgetall(keys.hold(hold_id))
        if not hold:
            raise NotFoundError(f"Reserva {hold_id} no existe o expiró")
        event = self.r.hgetall(keys.event(hold["event_id"]))
        if not event:
            raise NotFoundError(f"Evento {hold['event_id']} no existe")
        order_id = next_id(self.db, "order")
        member = keys.hold_member(hold_id, hold["event_id"], hold["user_id"], int(hold["qty"]))
        created_at = now_iso()
        activity_entry = json.dumps({
            "at": created_at, "type": "ORDER", "order_id": order_id,
            "user_id": hold["user_id"], "event": event["name"], "qty": int(hold["qty"]),
        })
        result = self.db.confirm_hold(
            keys=[keys.hold(hold_id), keys.holds_expiring(), keys.user_holds(hold["user_id"]),
                  keys.order(order_id), keys.orders_index(), keys.user_orders(hold["user_id"]),
                  keys.ranking_sales(), keys.activity()],
            args=[member, hold_id, order_id, event["price"], created_at,
                  activity_entry, settings.activity_max],
        )
        if int(result[0]) != 1:
            raise ConflictError("La reserva expiró justo antes de confirmar")
        self.r.delete(keys.event_summary_cache(hold["event_id"]))
        return self.get(order_id)

    def get(self, order_id: str) -> dict:
        data = self.r.hgetall(keys.order(order_id))
        if not data:
            raise NotFoundError(f"Orden {order_id} no existe")
        return self._hydrate(data)

    def list_for_user(self, user_id: str, limit: int = 20) -> list[dict]:
        # LRANGE sobre la lista LPUSH -> más recientes primero.
        ids = self.r.lrange(keys.user_orders(user_id), 0, limit - 1)
        pipe = self.r.pipeline(transaction=False)
        for order_id in ids:
            pipe.hgetall(keys.order(order_id))
        return [self._hydrate(o) for o in pipe.execute() if o]

    def list_all(self) -> list[dict]:
        ids = sorted(self.r.smembers(keys.orders_index()), key=int)
        pipe = self.r.pipeline(transaction=False)
        for order_id in ids:
            pipe.hgetall(keys.order(order_id))
        return [self._hydrate(o) for o in pipe.execute() if o]

    def cancel(self, order_id: str) -> dict:
        """Cancela una orden: devuelve stock y resta del ranking."""
        data = self.get(order_id)
        if data["status"] == "CANCELLED":
            raise ConflictError("La orden ya estaba cancelada")
        pipe = self.r.pipeline(transaction=True)
        pipe.hset(keys.order(order_id), "status", "CANCELLED")
        pipe.incrby(keys.event_stock(data["event_id"]), data["qty"])
        pipe.zincrby(keys.ranking_sales(), -data["qty"], data["event_id"])
        pipe.delete(keys.event_summary_cache(data["event_id"]))
        pipe.execute()
        return self.get(order_id)

    def delete(self, order_id: str) -> None:
        data = self.get(order_id)
        pipe = self.r.pipeline(transaction=True)
        pipe.delete(keys.order(order_id))
        pipe.srem(keys.orders_index(), order_id)
        pipe.lrem(keys.user_orders(data["user_id"]), 0, order_id)
        pipe.execute()

    @staticmethod
    def _hydrate(data: dict) -> dict:
        return {**data, "qty": int(data["qty"]), "unit_price": float(data["unit_price"]),
                "total": float(data["total"])}
