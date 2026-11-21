"""Punto único de acceso a las DOS bases de datos del sistema.

    Stores.pg     -> PostgreSQL (relacional): usuarios, eventos, órdenes, auditoría.
    Stores.redis  -> Redis (no relacional): stock en caliente, reservas, sesiones, caché.

Los repositorios reciben un `Stores` y deciden en qué base va cada dato.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from app.db import Database, get_db
from app.pg import Postgres, get_pg


@dataclass
class Stores:
    redis: Database
    pg: Postgres

    def health(self) -> dict:
        """Estado de ambas conexiones (para /health y la CLI)."""
        out: dict = {"redis_ok": False, "postgres_ok": False}
        try:
            out.update(self.redis.info_summary())
            out["redis_ok"] = self.redis.ping()
        except Exception as exc:  # noqa: BLE001 - se reporta, no se propaga
            out["redis_error"] = str(exc)
        try:
            out.update(self.pg.info_summary())
            out["postgres_ok"] = self.pg.ping()
        except Exception as exc:  # noqa: BLE001
            out["postgres_error"] = str(exc)
        out["ok"] = out["redis_ok"] and out["postgres_ok"]
        return out


@lru_cache(maxsize=1)
def get_stores() -> Stores:
    return Stores(redis=get_db(), pg=get_pg())
