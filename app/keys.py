"""Convención de nombres de claves de REDIS (key namespaces).

Todas las claves siguen el patrón `<prefijo>:<entidad>:<id>[:<sub-recurso>]`.
Centralizar los constructores aquí evita errores tipográficos y documenta el
modelo de datos no relacional en un solo lugar.

Redis guarda SOLO estado efímero o derivado; los datos permanentes (usuarios,
eventos, órdenes) viven en PostgreSQL y no tienen clave aquí.

Resumen del esquema (prefijo por defecto `tf`):

    tf:event:<id>:stock              STRING  entradas disponibles ahora (entero, atómico)
    tf:event:<id>:visitors           HLL     visitantes únicos (PFADD / PFCOUNT)
    tf:user:<id>:holds               SET     reservas activas del usuario
    tf:session:<token>               HASH    sesión con TTL deslizante (user_id, role)
    tf:hold:<id>                     HASH    reserva temporal con TTL fijo
    tf:holds:expiring                ZSET    miembro "hold:event:user:qty", score = expira_en
    tf:ranking:sales                 ZSET    event_id -> entradas vendidas
    tf:activity                      LIST    feed de actividad reciente (LTRIM)
    tf:cache:event:<id>:summary      STRING  JSON cacheado con TTL corto
    tf:ratelimit:login:<email>       STRING  intentos fallidos de login (INCR + EXPIRE)
"""

from __future__ import annotations

from app.config import settings

P = settings.key_prefix


# --- Eventos -----------------------------------------------------------------
def event_stock(event_id: str) -> str:
    return f"{P}:event:{event_id}:stock"


def event_visitors(event_id: str) -> str:
    return f"{P}:event:{event_id}:visitors"


def event_summary_cache(event_id: str) -> str:
    return f"{P}:cache:event:{event_id}:summary"


# --- Usuarios ----------------------------------------------------------------
def user_holds(user_id: str) -> str:
    return f"{P}:user:{user_id}:holds"


def login_attempts(email: str) -> str:
    return f"{P}:ratelimit:login:{email.strip().lower()}"


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


# --- Analítica ---------------------------------------------------------------
def ranking_sales() -> str:
    return f"{P}:ranking:sales"


def activity() -> str:
    return f"{P}:activity"


def all_pattern() -> str:
    """Patrón para SCAN al limpiar la base (solo scripts de mantenimiento)."""
    return f"{P}:*"
