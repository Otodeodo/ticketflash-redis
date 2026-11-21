"""Conexión a PostgreSQL: pool de conexiones y transacciones.

* Un pool (`psycopg_pool`) comparte conexiones entre los hilos de la API.
* `Postgres.run(fn)` ejecuta `fn(cursor)` dentro de UNA transacción: si `fn`
  termina bien se hace COMMIT; si lanza una excepción, ROLLBACK (todo o nada).
* Los fallos transitorios de concurrencia (deadlock, fallo de serialización) se
  reintentan automáticamente con una pequeña espera aleatoria.
"""

from __future__ import annotations

import random
import time
from functools import lru_cache
from typing import Callable, TypeVar

import psycopg
from psycopg import errors as pgerr
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import settings

T = TypeVar("T")

#: Errores que indican "vuelve a intentarlo": otra transacción ganó la carrera.
RETRYABLE = (pgerr.DeadlockDetected, pgerr.SerializationFailure)


class Postgres:
    """Envoltorio del pool de conexiones con ayudas transaccionales."""

    def __init__(self, pool: ConnectionPool):
        self.pool = pool

    # --- Transacciones --------------------------------------------------------
    def run(self, fn: Callable[[psycopg.Cursor], T], *, isolation: str | None = None,
            read_only: bool = False, retries: int = 3) -> T:
        """Ejecuta `fn(cursor)` en una transacción con reintentos ante deadlocks.

        isolation: None (READ COMMITTED, por defecto), "REPEATABLE READ" o "SERIALIZABLE".
        """
        attempt = 0
        while True:
            try:
                with self.pool.connection() as conn:   # COMMIT al salir, ROLLBACK si hay error
                    with conn.cursor() as cur:
                        if isolation or read_only:
                            mode = f"ISOLATION LEVEL {isolation}" if isolation else ""
                            ro = "READ ONLY" if read_only else ""
                            cur.execute(f"SET TRANSACTION {mode} {ro}".replace("  ", " "))
                        return fn(cur)
            except RETRYABLE:
                attempt += 1
                if attempt > retries:
                    raise
                time.sleep(random.uniform(0.02, 0.12) * attempt)

    def fetch_all(self, sql: str, params=None) -> list[dict]:
        return self.run(lambda cur: cur.execute(sql, params).fetchall(), read_only=True)

    def fetch_one(self, sql: str, params=None) -> dict | None:
        return self.run(lambda cur: cur.execute(sql, params).fetchone(), read_only=True)

    # --- Estado ---------------------------------------------------------------
    def ping(self) -> bool:
        row = self.fetch_one("SELECT 1 AS ok")
        return bool(row and row["ok"] == 1)

    def info_summary(self) -> dict:
        row = self.fetch_one(
            "SELECT current_setting('server_version') AS version, current_user AS db_user, "
            "pg_size_pretty(pg_database_size(current_database())) AS size")
        return {"pg_version": row["version"], "pg_user": row["db_user"], "pg_size": row["size"]}

    def table_counts(self) -> dict:
        row = self.fetch_one(
            "SELECT (SELECT count(*) FROM users) AS users, (SELECT count(*) FROM events) AS events, "
            "(SELECT count(*) FROM orders) AS orders, (SELECT count(*) FROM audit_log) AS audit")
        return {k: int(v) for k, v in row.items()}

    def close(self) -> None:
        self.pool.close()


def _configure(conn: psycopg.Connection) -> None:
    # Cinturón y tirantes: el rol ya fija search_path, pero algunos pools lo ignoran.
    conn.execute("SET search_path TO ticketflash, public")
    conn.commit()


def make_postgres(url: str | None = None, *, min_size: int | None = None,
                  max_size: int | None = None) -> Postgres:
    """Crea el pool. `prepare_threshold=None` evita sentencias preparadas, que
    no funcionan con pgbouncer en modo transacción (usado por Neon/Supabase)."""
    url = url or settings.database_url
    if not url:
        raise RuntimeError("Falta DATABASE_URL: define la conexión a PostgreSQL en .env")
    pool = ConnectionPool(
        url,
        min_size=min_size if min_size is not None else settings.pg_pool_min,
        max_size=max_size if max_size is not None else settings.pg_pool_max,
        kwargs={"row_factory": dict_row, "prepare_threshold": None,
                "connect_timeout": settings.pg_connect_timeout},
        configure=_configure,
        open=False,
    )
    pool.open(wait=True, timeout=settings.pg_connect_timeout + 5)
    return Postgres(pool)


@lru_cache(maxsize=1)
def get_pg() -> Postgres:
    """Instancia única usada por la API y la CLI."""
    return make_postgres()
