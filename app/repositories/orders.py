"""Repositorio de órdenes: transacción ACID en PostgreSQL + coordinación con Redis.

Confirmar una compra toca las DOS bases de datos, que no comparten transacción.
Se resuelve con el patrón SAGA (pasos con compensación):

    1. Redis  claim_hold   (Lua, atómico)  -> la reserva se "reclama": solo un cliente puede.
    2. PostgreSQL  BEGIN
          UPDATE events SET sold = sold + qty WHERE id = ? AND sold + qty <= capacity
          INSERT INTO orders (...)         -> UNIQUE(hold_id) = idempotencia
       COMMIT                               (todo o nada)
    3a. Si PostgreSQL falla  -> COMPENSACIÓN: Redis INCRBY stock (devuelve las entradas).
    3b. Si todo va bien      -> Redis: ranking (ZINCRBY), feed (LPUSH), invalidar caché.

Si el proceso muere entre 1 y 2, las entradas quedan "perdidas" en Redis; el
reconciliador (`maintenance.reconcile`) recalcula el stock desde PostgreSQL.
"""

from __future__ import annotations

import json

from psycopg import errors as pgerr

from app import keys
from app.config import settings
from app.repositories.common import (ConflictError, ForbiddenError, NotFoundError, now_iso,
                                     to_int_id)
from app.stores import Stores

_COLS = "id, user_id, event_id, qty, unit_price, total, status, hold_id, created_at, cancelled_at"


def _public(row: dict) -> dict:
    return {
        "id": str(row["id"]), "user_id": str(row["user_id"]), "event_id": str(row["event_id"]),
        "qty": row["qty"], "unit_price": float(row["unit_price"]), "total": float(row["total"]),
        "status": row["status"], "created_at": row["created_at"].isoformat(timespec="seconds"),
    }


class OrderRepository:
    def __init__(self, stores: Stores):
        self.pg = stores.pg
        self.r = stores.redis.client
        self.db = stores.redis

    # --- Confirmar -----------------------------------------------------------------
    def confirm(self, hold_id: str, *, user_id: str | None = None) -> dict:
        hold = self.r.hgetall(keys.hold(hold_id))
        if not hold:
            raise NotFoundError(f"Reserva {hold_id} no existe o expiró")
        if user_id is not None and hold["user_id"] != user_id:
            raise ForbiddenError("Esa reserva pertenece a otro usuario")
        event_id, owner, qty = hold["event_id"], hold["user_id"], int(hold["qty"])
        member = keys.hold_member(hold_id, event_id, owner, qty)

        # Paso 1 (Redis): reclamar la reserva de forma atómica.
        claimed = self.db.claim_hold(
            keys=[keys.hold(hold_id), keys.holds_expiring(), keys.user_holds(owner)],
            args=[member, hold_id])
        if int(claimed) != 1:
            raise ConflictError("La reserva expiró o ya fue confirmada")

        # Paso 2 (PostgreSQL): una transacción ACID.
        def tx(cur):
            ev = cur.execute(
                "UPDATE events SET sold = sold + %s, version = version + 1 "
                "WHERE id = %s AND sold + %s <= capacity RETURNING name, price",
                (qty, int(event_id), qty)).fetchone()
            if not ev:
                raise ConflictError("No quedan entradas disponibles en la base de datos")
            return ev, cur.execute(
                f"INSERT INTO orders (user_id, event_id, qty, unit_price, hold_id) "
                f"VALUES (%s, %s, %s, %s, %s) RETURNING {_COLS}",
                (int(owner), int(event_id), qty, ev["price"], hold_id)).fetchone()
        try:
            event, row = self.pg.run(tx)
        except Exception as exc:
            # Paso 3a: compensación. Las entradas reservadas vuelven al stock de Redis.
            self.r.incrby(keys.event_stock(event_id), qty)
            if isinstance(exc, pgerr.UniqueViolation):
                raise ConflictError("La reserva ya fue confirmada") from exc
            if isinstance(exc, pgerr.ForeignKeyViolation):
                raise NotFoundError("El usuario o el evento ya no existen") from exc
            raise

        # Paso 3b: efectos derivados en Redis (si fallan, el reconciliador los repara).
        order = _public(row)
        entry = json.dumps({"at": now_iso(), "type": "ORDER", "order_id": order["id"],
                            "user_id": owner, "event": event["name"], "qty": qty})
        pipe = self.r.pipeline(transaction=True)
        pipe.zincrby(keys.ranking_sales(), qty, event_id)
        pipe.lpush(keys.activity(), entry)
        pipe.ltrim(keys.activity(), 0, settings.activity_max - 1)
        pipe.delete(keys.event_summary_cache(event_id))
        pipe.execute()
        return order

    # --- Consultas -------------------------------------------------------------------
    def get(self, order_id: str) -> dict:
        row = self.pg.fetch_one(f"SELECT {_COLS} FROM orders WHERE id = %s",
                                (to_int_id(order_id, "Orden"),))
        if not row:
            raise NotFoundError(f"Orden {order_id} no existe")
        return _public(row)

    def list_for_user(self, user_id: str, limit: int = 20) -> list[dict]:
        # Índice (user_id, created_at DESC): devuelve las más recientes sin ordenar.
        try:
            uid = to_int_id(user_id, "Usuario")
        except NotFoundError:
            return []
        rows = self.pg.fetch_all(
            f"SELECT {_COLS} FROM orders WHERE user_id = %s ORDER BY created_at DESC, id DESC LIMIT %s",
            (uid, limit))
        return [_public(r) for r in rows]

    def list_all(self, limit: int = 200) -> list[dict]:
        rows = self.pg.fetch_all(f"SELECT {_COLS} FROM orders ORDER BY id DESC LIMIT %s", (limit,))
        return [_public(r) for r in rows]

    # --- Cancelar ----------------------------------------------------------------------
    def cancel(self, order_id: str, *, user_id: str | None = None) -> dict:
        """Cancela una orden: transacción en PostgreSQL y luego stock/ranking en Redis."""
        oid = to_int_id(order_id, "Orden")

        def tx(cur):
            # FOR UPDATE: serializa cancelaciones simultáneas de la MISMA orden.
            row = cur.execute(f"SELECT {_COLS} FROM orders WHERE id = %s FOR UPDATE", (oid,)).fetchone()
            if not row:
                raise NotFoundError(f"Orden {order_id} no existe")
            if user_id is not None and str(row["user_id"]) != user_id:
                raise ForbiddenError("Esa orden pertenece a otro usuario")
            if row["status"] == "CANCELLED":
                raise ConflictError("La orden ya estaba cancelada")
            cur.execute("UPDATE orders SET status = 'CANCELLED', cancelled_at = now() WHERE id = %s", (oid,))
            cur.execute("UPDATE events SET sold = sold - %s, version = version + 1 WHERE id = %s",
                        (row["qty"], row["event_id"]))
            return row
        row = self.pg.run(tx)
        event_id = str(row["event_id"])
        pipe = self.r.pipeline(transaction=True)
        pipe.incrby(keys.event_stock(event_id), row["qty"])
        pipe.zincrby(keys.ranking_sales(), -row["qty"], event_id)
        pipe.delete(keys.event_summary_cache(event_id))
        pipe.execute()
        return self.get(order_id)
