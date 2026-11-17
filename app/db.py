"""Conexión a Redis y scripts Lua atómicos.

Un único cliente `redis.Redis` (con pool de conexiones interno) es compartido
por toda la aplicación. Los scripts Lua se registran una vez y Redis los
ejecuta de forma atómica (EVALSHA), lo que evita condiciones de carrera entre
clientes concurrentes sin necesidad de bloqueos.
"""

from __future__ import annotations

from functools import lru_cache

import redis

from app.config import settings

# ---------------------------------------------------------------------------
# Scripts Lua
# ---------------------------------------------------------------------------

#: Crea una reserva temporal descontando stock de forma atómica.
#: KEYS: 1=stock, 2=hold, 3=holds_expiring, 4=user_holds
#: ARGV: 1=hold_id, 2=user_id, 3=event_id, 4=qty, 5=ttl, 6=now, 7=member, 8=created_at
#: Devuelve {ok, stock_restante}
LUA_CREATE_HOLD = """
local stock = tonumber(redis.call('GET', KEYS[1]) or '0')
local qty = tonumber(ARGV[4])
if qty <= 0 then return {0, stock} end
if stock < qty then return {0, stock} end
local ttl = tonumber(ARGV[5])
local expires_at = tonumber(ARGV[6]) + ttl
redis.call('DECRBY', KEYS[1], qty)
redis.call('HSET', KEYS[2],
    'id', ARGV[1], 'user_id', ARGV[2], 'event_id', ARGV[3], 'qty', ARGV[4],
    'created_at', ARGV[8], 'expires_at', tostring(expires_at))
redis.call('EXPIRE', KEYS[2], ttl)
redis.call('ZADD', KEYS[3], expires_at, ARGV[7])
redis.call('SADD', KEYS[4], ARGV[1])
return {1, stock - qty}
"""

#: Libera una reserva (cancelación o expiración) devolviendo el stock.
#: Usa ZREM como "cerrojo": solo quien logra eliminar el miembro del ZSET
#: devuelve el stock, así una misma reserva nunca se restaura dos veces.
#: KEYS: 1=hold, 2=holds_expiring, 3=stock, 4=user_holds
#: ARGV: 1=member, 2=hold_id, 3=qty
#: Devuelve 1 si se liberó, 0 si ya no existía.
LUA_RELEASE_HOLD = """
if redis.call('ZREM', KEYS[2], ARGV[1]) == 0 then return 0 end
redis.call('INCRBY', KEYS[3], tonumber(ARGV[3]))
redis.call('DEL', KEYS[1])
redis.call('SREM', KEYS[4], ARGV[2])
return 1
"""

#: Convierte una reserva vigente en una orden confirmada.
#: KEYS: 1=hold, 2=holds_expiring, 3=user_holds, 4=order, 5=orders_index,
#:       6=user_orders, 7=ranking_sales, 8=activity
#: ARGV: 1=member, 2=hold_id, 3=order_id, 4=unit_price, 5=created_at,
#:       6=activity_entry, 7=activity_max
#: Devuelve {ok, event_id, qty, total}
LUA_CONFIRM_HOLD = """
if redis.call('EXISTS', KEYS[1]) == 0 then return {0} end
if redis.call('ZREM', KEYS[2], ARGV[1]) == 0 then return {0} end
local h = redis.call('HMGET', KEYS[1], 'user_id', 'event_id', 'qty')
local user_id, event_id, qty = h[1], h[2], tonumber(h[3])
local total = qty * tonumber(ARGV[4])
redis.call('DEL', KEYS[1])
redis.call('SREM', KEYS[3], ARGV[2])
redis.call('HSET', KEYS[4],
    'id', ARGV[3], 'user_id', user_id, 'event_id', event_id, 'qty', tostring(qty),
    'unit_price', ARGV[4], 'total', tostring(total), 'created_at', ARGV[5],
    'status', 'CONFIRMED')
redis.call('SADD', KEYS[5], ARGV[3])
redis.call('LPUSH', KEYS[6], ARGV[3])
redis.call('ZINCRBY', KEYS[7], qty, event_id)
redis.call('LPUSH', KEYS[8], ARGV[6])
redis.call('LTRIM', KEYS[8], 0, tonumber(ARGV[7]) - 1)
return {1, event_id, qty, tostring(total)}
"""


class Database:
    """Envoltorio del cliente redis-py con los scripts registrados."""

    def __init__(self, client: redis.Redis):
        self.client = client
        self.create_hold = client.register_script(LUA_CREATE_HOLD)
        self.release_hold = client.register_script(LUA_RELEASE_HOLD)
        self.confirm_hold = client.register_script(LUA_CONFIRM_HOLD)

    def ping(self) -> bool:
        return bool(self.client.ping())

    def info_summary(self) -> dict:
        """Datos básicos del servidor para la pantalla de estado."""
        try:
            info = self.client.info("server")
            memory = self.client.info("memory")
        except redis.exceptions.ResponseError:
            # Algunos entornos (p. ej. fakeredis) no implementan INFO.
            info, memory = {}, {}
        return {
            "redis_version": info.get("redis_version", "n/a"),
            "mode": info.get("redis_mode", "n/a"),
            "used_memory_human": memory.get("used_memory_human", "n/a"),
            "db_size": self.client.dbsize(),
        }


def make_client(**overrides) -> redis.Redis:
    """Crea el cliente a partir de la configuración (o de overrides)."""
    kwargs = {**settings.connection_kwargs, **overrides}
    url = kwargs.pop("url", None)
    if url:
        return redis.Redis.from_url(url, **kwargs)
    return redis.Redis(**kwargs)


@lru_cache(maxsize=1)
def get_db() -> Database:
    """Instancia única (singleton) usada por la API y la CLI."""
    return Database(make_client())
