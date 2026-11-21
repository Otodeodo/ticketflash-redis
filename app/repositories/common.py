"""Utilidades compartidas por los repositorios."""

from __future__ import annotations

import secrets
import time
from datetime import datetime, timezone


class NotFoundError(Exception):
    """La entidad solicitada no existe."""


class ConflictError(Exception):
    """La operación no puede completarse por el estado actual (p. ej. sin stock)."""


class ForbiddenError(Exception):
    """El usuario autenticado no tiene permiso sobre el recurso."""


class InvalidCredentials(Exception):
    """Correo o contraseña incorrectos."""


class TooManyAttempts(Exception):
    """Demasiados intentos fallidos de inicio de sesión (límite en Redis)."""


def now_ts() -> int:
    """Marca de tiempo Unix en segundos (usada como score en ZSETs)."""
    return int(time.time())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_hold_id() -> str:
    """Id aleatorio de reserva. No depende de un contador de Redis, así que sigue
    siendo único aunque Redis se reinicie (la orden lo guarda con índice UNIQUE)."""
    return secrets.token_hex(5)


def to_int_id(value: str | int, what: str = "Recurso") -> int:
    """Convierte un id recibido por la API; un id no numérico equivale a 'no existe'."""
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise NotFoundError(f"{what} {value} no existe") from exc
    if not 0 < number < 2 ** 63:
        raise NotFoundError(f"{what} {value} no existe")
    return number
