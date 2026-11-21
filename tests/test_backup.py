"""Prueba extremo a extremo del respaldo: pg_dump real + exportación de Redis + verificación + restauración."""

from __future__ import annotations

import argparse
import zipfile

import fakeredis
import psycopg
import pytest

from app import keys
from app.repositories import EventRepository, HoldRepository, OrderRepository, UserRepository
from scripts import backup


@pytest.fixture
def seeded(stores, monkeypatch):
    """Datos en ambas bases y un Redis falso compartido que el script de respaldo usará."""
    server = fakeredis.FakeServer()
    monkeypatch.setattr(backup, "make_redis", lambda url, **kw: fakeredis.FakeRedis(server=server, **kw))
    stores.redis.client = fakeredis.FakeRedis(server=server, decode_responses=True)
    stores.redis.__init__(stores.redis.client)          # registra de nuevo los scripts Lua
    user = UserRepository(stores).create(name="Ana", email="ana@example.com", password="clave-segura-123")
    ev = EventRepository(stores).create(name="Show", venue="Sala", date="2026-12-01T20:00",
                                        category="musica", price=10.0, capacity=10)
    hold = HoldRepository(stores).create(user_id=user["id"], event_id=ev["id"], qty=2)
    OrderRepository(stores).confirm(hold["id"])
    stores.redis.client.set(keys.session("secreto"), "no-debe-respaldarse")   # sesión: se excluye
    return user, ev


def _args(**kw):
    return argparse.Namespace(**kw)


def test_backup_verify_and_restore_roundtrip(stores, pg_urls, seeded, tmp_path, monkeypatch):
    monkeypatch.delenv("BACKUP_S3_BUCKET", raising=False)
    monkeypatch.delenv("BACKUP_RCLONE_REMOTE", raising=False)
    zip_path = backup.cmd_backup(_args(pg_url=pg_urls["backup"], redis_url="redis://fake", out=str(tmp_path)))
    assert zip_path.exists()

    with zipfile.ZipFile(zip_path) as z:
        assert set(z.namelist()) == {"postgres.dump", "redis.jsonl.gz", "manifest.json"}
    manifest = backup._extract(zip_path, tmp_path / "x")
    assert manifest["postgres"]["rows"]["orders"] == 1 and manifest["postgres"]["rows"]["users"] == 1
    assert manifest["redis"]["keys"] >= 2                           # stock, ranking, actividad...
    redis_file = (tmp_path / "x" / "redis.jsonl.gz")
    import gzip
    assert "secreto" not in gzip.open(redis_file, "rt").read()      # sesiones fuera del respaldo

    # Verificación en una base temporal (no toca la real).
    backup.cmd_verify(_args(zip=str(zip_path), admin_url=pg_urls["admin"]))

    # Desastre: se pierden datos en ambas bases.
    with psycopg.connect(pg_urls["admin"], autocommit=True) as conn:
        conn.execute("TRUNCATE ticketflash.orders, ticketflash.events, ticketflash.users, ticketflash.venues, "
                     "ticketflash.categories, ticketflash.audit_log RESTART IDENTITY CASCADE")
    stores.redis.client.flushall()
    assert stores.pg.table_counts()["orders"] == 0

    # Restauración completa.
    monkeypatch.setenv("TF_APP_PASSWORD", "test-app-pw")
    monkeypatch.setenv("TF_ANALYST_PASSWORD", "test-analyst-pw")
    monkeypatch.setenv("TF_BACKUP_PASSWORD", "test-backup-pw")
    backup.cmd_restore(_args(zip=str(zip_path), pg_admin_url=pg_urls["admin"], redis_url="redis://fake", yes=True))
    counts = stores.pg.table_counts()
    assert counts["orders"] == 1 and counts["users"] == 1 and counts["events"] == 1
    assert stores.redis.client.exists(keys.ranking_sales()) == 1
    # El rol de la aplicación sigue funcionando tras restaurar (privilegios reaplicados).
    assert EventRepository(stores).list_all()[0]["sold"] == 2


def test_corrupted_backup_is_detected(stores, pg_urls, seeded, tmp_path, monkeypatch):
    zip_path = backup.cmd_backup(_args(pg_url=pg_urls["backup"], redis_url="redis://fake", out=str(tmp_path)))
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(zip_path) as src, zipfile.ZipFile(bad, "w") as dst:
        for name in src.namelist():
            data = src.read(name)
            dst.writestr(name, data + b"x" if name == "postgres.dump" else data)
    with pytest.raises(SystemExit):
        backup._extract(bad, tmp_path / "y")


def test_restore_requires_confirmation(tmp_path):
    with pytest.raises(SystemExit):
        backup.cmd_restore(_args(zip="x.zip", pg_admin_url=None, redis_url=None, yes=False))
