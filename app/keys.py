"""Convención de nombres de claves (key namespaces).

Todas las claves siguen el patrón `<prefijo>:<entidad>:<id>[:<sub-recurso>]`.
Centralizar los constructores aquí evita errores tipográficos y documenta el
modelo de datos en un solo lugar.

Resumen del esquema (prefijo por defecto `tf`):

    tf:seq:<entidad>                 STRING  contador INCR para generar ids
    tf:event:<id>                    HASH    datos del evento
    tf:event:<id>:stock              STRING  entradas disponibles (entero, atómico)
    tf:event:<id>:visitors           HLL     visitantes únicos (PFADD / PFCOUNT)
    tf:events                        SET     índice de ids de eventos
    tf:events:category:<cat>         SET     índice secundario por categoría
    tf:user:<id>                     HASH    datos del usuario
    tf:users                         SET     índice de ids de usuarios
    tf:user:<id>:orders              LIST    historial de ids de órdenes (LPUSH)
    tf:user:<id>:holds               SET     reservas activas del usuario
    tf:session:<token>               HASH    sesión con TTL deslizante
    tf:hold:<id>                     HASH    reserva temporal con TTL fijo
    tf:holds:expiring                ZSET    miembro "hold:event:user:qty", score = expira_en
    tf:order:<id>                    HASH    orden confirmada (sin TTL)
    tf:orders                        SET     índice de ids de órdenes
    tf:ranking:sales                 ZSET    event_id -> entradas vendidas
    tf:activity                      LIST    feed de actividad reciente (LTRIM)
    tf:cache:event:<id>:summary      STRING  JSON cacheado con TTL corto
"""

from __future__ import annotations

from app.config import settings

P = settings.key_prefix


def seq(entity: str) -> str:
    return f"{P}:seq:{entity}"


# --- Eventos -----------------------------------------------------------------
def event(event_id: str) -> str:
    return f"{P}:event:{event_id}"


def event_stock(event_id: str) -> str:
    return f"{P}:event:{event_id}:stock"


def event_visitors(event_id: str) -> str:
    return f"{P}:event:{event_id}:visitors"


def events_index() -> str:
    return f"{P}:events"


def events_by_category(category: str) -> str:
    return f"{P}:events:category:{category.lower()}"


def event_summary_cache(event_id: str) -> str:
    return f"{P}:cache:event:{event_id}:summary"


# --- Usuarios ----------------------------------------------------------------
def user(user_id: str) -> str:
    return f"{P}:user:{user_id}"


def users_index() -> str:
    return f"{P}:users"


def user_orders(user_id: str) -> str:
    return f"{P}:user:{user_id}:orders"


def user_holds(user_id: str) -> str:
    return f"{P}:user:{user_id}:holds"


# --- Sesiones ----------------------------------------------------------------
def session(token: str) -> str:
    return f"{P}:session:{token}"


# --- Reservas temporales (holds) ---------------------------------------------
def hold(hold_id: str) -> str:
    return f"{P}:hold:{hold_id}"


def holds_expiring() -> str:
    return f"{P}:holds:expiring"


def hold_member(hold_id: str, event_id: str, user_id: str, qty: int) -> str:
    """Miembro del ZSET de expiración. Codifica lo necesario para devolver el
    stock aunque la clave `hold:<id>` ya haya expirado y desaparecido."""
    return f"{hold_id}:{event_id}:{user_id}:{qty}"


def parse_hold_member(member: str) -> tuple[str, str, str, int]:
    hold_id, event_id, user_id, qty = member.split(":")
    return hold_id, event_id, user_id, int(qty)


# --- Órdenes -----------------------------------------------------------------
def order(order_id: str) -> str:
    return f"{P}:order:{order_id}"


def orders_index() -> str:
    return f"{P}:orders"


# --- Analítica ---------------------------------------------------------------
def ranking_sales() -> str:
    return f"{P}:ranking:sales"


def activity() -> str:
    return f"{P}:activity"


def all_pattern() -> str:
    """Patrón para SCAN al limpiar la base (solo scripts de mantenimiento)."""
    return f"{P}:*"
