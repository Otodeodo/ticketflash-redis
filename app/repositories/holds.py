"""Repositorio de reservas temporales (holds) — Redis.

Una reserva bloquea entradas durante `HOLD_TTL_SECONDS`. Si el usuario no
confirma a tiempo, la clave HASH expira sola gracias al TTL y el stock se
devuelve con `reclaim_expired()`.

Estructuras:
  * HASH tf:hold:<id>        con EXPIRE   -> datos de la reserva
  * ZSET tf:holds:expiring   score=expira -> cola ordenada por expiración
  * SET  tf:user:<id>:holds               -> reservas activas por usuario

Por qué dos estructuras: cuando Redis expira `tf:hold:<id>` ya no podemos
leer cuántas entradas bloqueaba. Por eso el miembro del ZSET codifica
"hold_id:event_id:user_id:qty" y así podemos devolver el stock sin la clave original.

Concurrencia: crear una reserva es un script Lua (comprobar + descontar +
registrar en una sola operación atómica). Redis ejecuta un script a la vez, así
que 1000 clientes compitiendo por las últimas entradas nunca sobrevenden.
"""

from __future__ import annotations

from app import keys
from app.config import settings
from app.repositories.common import (ConflictError, ForbiddenError, NotFoundError, new_hold_id,
                                     now_iso, now_ts)
from app.repositories.events import EventRepository
from app.repositories.users import UserRepository
from app.stores import Stores


class HoldRepository:
    def __init__(self, stores: Stores):
        self.stores = stores
        self.db = stores.redis
        self.r = stores.redis.client
        self.events = EventRepository(stores)
        self.users = UserRepository(stores)

    def create(self, *, user_id: str, event_id: str, qty: int) -> dict:
        if not self.users.exists(user_id):
            raise NotFoundError(f"Usuario {user_id} no existe")
        self.events.ensure_stock(event_id)        # valida el evento (PostgreSQL) y calienta Redis
        self.reclaim_expired()                    # liberar primero lo que ya venció
        hold_id = new_hold_id()
        member = keys.hold_member(hold_id, event_id, user_id, qty)
        ok, remaining = self.db.create_hold(
            keys=[keys.event_stock(event_id), keys.hold(hold_id),
                  keys.holds_expiring(), keys.user_holds(user_id)],
            args=[hold_id, user_id, event_id, qty, settings.hold_ttl, now_ts(),
                  member, now_iso()],
        )
        if int(ok) != 1:
            raise ConflictError(f"Stock insuficiente: quedan {remaining} entradas")
        return self.get(hold_id)

    def get(self, hold_id: str) -> dict:
        key = keys.hold(hold_id)
        pipe = self.r.pipeline(transaction=True)
        pipe.hgetall(key)
        pipe.ttl(key)
        data, ttl = pipe.execute()
        if not data:
            raise NotFoundError(f"Reserva {hold_id} no existe o expiró")
        return {**data, "qty": int(data["qty"]), "ttl_seconds": int(ttl)}

    def list_for_user(self, user_id: str) -> list[dict]:
        self.reclaim_expired()
        result = []
        for hold_id in self.r.smembers(keys.user_holds(user_id)):
            try:
                result.append(self.get(hold_id))
            except NotFoundError:
                continue
        return sorted(result, key=lambda h: h["created_at"])

    def cancel(self, hold_id: str, *, user_id: str | None = None) -> dict:
        data = self.get(hold_id)
        if user_id is not None and data["user_id"] != user_id:
            raise ForbiddenError("Esa reserva pertenece a otro usuario")
        member = keys.hold_member(hold_id, data["event_id"], data["user_id"], data["qty"])
        released = self.db.release_hold(
            keys=[keys.hold(hold_id), keys.holds_expiring(),
                  keys.event_stock(data["event_id"]), keys.user_holds(data["user_id"])],
            args=[member, hold_id, data["qty"]],
        )
        return {"hold_id": hold_id, "released": int(released) == 1, "qty": data["qty"]}

    def reclaim_expired(self) -> int:
        """Devuelve el stock de reservas cuyo TTL ya venció (expiración perezosa).

        ZRANGEBYSCORE obtiene en O(log N) las reservas con score <= ahora.
        """
        expired = self.r.zrangebyscore(keys.holds_expiring(), "-inf", now_ts())
        released = 0
        for member in expired:
            hold_id, event_id, user_id, qty = keys.parse_hold_member(member)
            released += int(self.db.release_hold(
                keys=[keys.hold(hold_id), keys.holds_expiring(),
                      keys.event_stock(event_id), keys.user_holds(user_id)],
                args=[member, hold_id, qty],
            ))
        return released

    def pending(self) -> list[dict]:
        """Reservas activas ordenadas por proximidad de expiración."""
        now = now_ts()
        rows = self.r.zrange(keys.holds_expiring(), 0, -1, withscores=True)
        return [
            {"hold_id": h, "event_id": e, "user_id": u, "qty": q, "expires_in": int(score) - now}
            for member, score in rows
            for (h, e, u, q) in [keys.parse_hold_member(member)]
        ]
