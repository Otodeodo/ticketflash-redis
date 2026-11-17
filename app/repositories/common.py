"""Utilidades compartidas por los repositorios."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from app import keys
from app.db import Database


class NotFoundError(Exception):
    """La entidad solicitada no existe en Redis."""


class ConflictError(Exception):
    """La operación no puede completarse por el estado actual (p. ej. sin stock)."""


def now_ts() -> int:
    """Marca de tiempo Unix en segundos (usada como score en ZSETs)."""
    return int(time.time())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def next_id(db: Database, entity: str) -> str:
    """Genera un id secuencial con INCR (operación atómica)."""
    return str(db.client.incr(keys.seq(entity)))
