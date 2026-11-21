"""Fixtures de prueba.

* Redis  -> fakeredis (con soporte Lua): no toca la nube.
* PostgreSQL -> un servidor REAL y temporal (paquete `pgserver`), o el que indique
  TEST_ADMIN_DATABASE_URL (usuario dueño de una base de pruebas vacía).
  El esquema y los roles se crean con los mismos scripts sql/ que en producción y la
  aplicación se conecta como `tf_app`, así las pruebas validan también los privilegios.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import fakeredis
import psycopg
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import Database  # noqa: E402
from app.pg import make_postgres  # noqa: E402
from app.stores import Stores  # noqa: E402
from scripts.init_db import init_database  # noqa: E402

APP_PW, ANALYST_PW, BACKUP_PW = "test-app-pw", "test-analyst-pw", "test-backup-pw"


def _with_credentials(url: str, user: str, password: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or "127.0.0.1"
    netloc = f"{user}:{password}@{host}" + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


@pytest.fixture(scope="session")
def pg_urls(tmp_path_factory):
    """Devuelve dict con admin/app/analyst/backup URLs de una base de pruebas lista."""
    admin_url = os.getenv("TEST_ADMIN_DATABASE_URL")
    if not admin_url:
        try:
            import pgserver
        except ImportError:
            pytest.skip("Instala `pgserver` o define TEST_ADMIN_DATABASE_URL para las pruebas de PostgreSQL")
        server = pgserver.get_server(tmp_path_factory.mktemp("pgdata"), cleanup_mode="stop")
        base = server.get_uri()
        with psycopg.connect(base, autocommit=True) as conn:
            conn.execute("CREATE DATABASE tf_test")
        parts = urlsplit(base)
        admin_url = urlunsplit((parts.scheme, parts.netloc, "/tf_test", "", ""))
    init_database(admin_url, app_password=APP_PW, analyst_password=ANALYST_PW, backup_password=BACKUP_PW)
    return {"admin": admin_url,
            "app": _with_credentials(admin_url, "tf_app", APP_PW),
            "analyst": _with_credentials(admin_url, "tf_analyst", ANALYST_PW),
            "backup": _with_credentials(admin_url, "tf_backup", BACKUP_PW)}


@pytest.fixture(scope="session")
def pg(pg_urls):
    postgres = make_postgres(pg_urls["app"], min_size=1, max_size=12)
    yield postgres
    postgres.close()


@pytest.fixture
def stores(pg, pg_urls) -> Stores:
    """Estado limpio en ambas bases antes de cada prueba."""
    with psycopg.connect(pg_urls["admin"], autocommit=True) as conn:
        conn.execute("TRUNCATE ticketflash.orders, ticketflash.events, ticketflash.users, "
                     "ticketflash.venues, ticketflash.categories, ticketflash.audit_log "
                     "RESTART IDENTITY CASCADE")
    return Stores(redis=Database(fakeredis.FakeRedis(decode_responses=True)), pg=pg)


@pytest.fixture
def db(stores) -> Database:
    return stores.redis
