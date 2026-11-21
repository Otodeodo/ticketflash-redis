"""Respaldo y restauración remotos de AMBAS bases de datos.

    python scripts/backup.py backup                 # crea, empaqueta, sube y aplica retención
    python scripts/backup.py list                   # respaldos locales (y remotos si hay destino)
    python scripts/backup.py verify <zip> --admin-url <pg_admin_url>
                                                   # prueba de restauración en una BD temporal
    python scripts/backup.py restore <zip> --pg-admin-url <url> --redis-url <url> --yes

Qué respalda
  * PostgreSQL : pg_dump formato custom (esquema + datos + vistas + triggers) del esquema
                 `ticketflash`, con el usuario de SOLO LECTURA `tf_backup`.
  * Redis      : todas las claves `tf:*` con DUMP + TTL (sin sesiones ni contadores de
                 login: son secretos/efímeros). Funciona también en Redis Cloud, donde no
                 se puede ejecutar SAVE/BGSAVE. Formato .jsonl.gz restaurable con RESTORE.
  * manifest.json con versiones, conteos y SHA-256 de cada archivo.

Destino remoto (opcional; se configura en .env, ver .env.example)
  * S3 / Cloudflare R2 / Backblaze B2   -> BACKUP_S3_* (requiere `pip install boto3`)
  * rclone (Google Drive, OneDrive...)  -> BACKUP_RCLONE_REMOTE

Programación diaria: scripts/schedule_backup.ps1 (Windows) o cron (Linux), ver README.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

import psycopg
import redis
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

SKIP_PREFIXES = ("session:", "ratelimit:")           # secretos / efímeros: nunca se respaldan
TABLES = ("users", "events", "orders", "audit_log", "categories", "venues")


# ------------------------------------------------------------------ utilidades
def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_pg_tool(name: str) -> str:
    """Localiza pg_dump/pg_restore/psql: PG_BIN, PATH o el que trae el paquete pgserver."""
    exe = name + (".exe" if os.name == "nt" else "")
    if os.getenv("PG_BIN") and (Path(os.environ["PG_BIN"]) / exe).exists():
        return str(Path(os.environ["PG_BIN"]) / exe)
    found = shutil.which(name)
    if found:
        return found
    try:
        import pgserver
        bundled = Path(pgserver.__file__).parent / "pginstall" / "bin" / exe
        if bundled.exists():
            return str(bundled)
    except ImportError:
        pass
    sys.exit(f"No se encontró '{name}'. Instala los clientes de PostgreSQL (misma versión mayor "
             f"que el servidor) o define PG_BIN con la carpeta bin.")


def pg_args(url: str) -> tuple[list[str], dict]:
    """Convierte una URL en argumentos de libpq + variables de entorno (la clave no va en la línea de comandos)."""
    u = urlsplit(url)
    env = dict(os.environ)
    if u.password:
        env["PGPASSWORD"] = unquote(u.password)
    if "sslmode=" in (u.query or ""):
        env["PGSSLMODE"] = dict(p.split("=", 1) for p in u.query.split("&") if "=" in p)["sslmode"]
    args = ["-h", u.hostname or "localhost", "-p", str(u.port or 5432),
            "-U", unquote(u.username or "postgres"), "-d", (u.path or "/postgres").lstrip("/")]
    return args, env


def run(cmd: list[str], env: dict) -> None:
    res = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if res.returncode != 0:
        sys.exit(f"Falló {Path(cmd[0]).name}:\n{res.stderr.strip()}")


def pg_counts(url: str) -> dict:
    with psycopg.connect(url, connect_timeout=15) as conn:
        return {t: conn.execute(f"SELECT count(*) FROM ticketflash.{t}").fetchone()[0] for t in TABLES}


# ----------------------------------------------------------------------- Redis
def make_redis(url: str, **kwargs) -> redis.Redis:
    """Cliente en modo binario (DUMP/RESTORE devuelven bytes). Punto de inyección para pruebas."""
    return redis.Redis.from_url(url, socket_timeout=30, **kwargs)


def redis_export(url: str, prefix: str, out: Path) -> int:
    """Exporta claves `<prefix>:*` a .jsonl.gz usando DUMP (conserva tipo, valor y TTL)."""
    client = make_redis(url)
    skip = tuple(f"{prefix}:{p}" for p in SKIP_PREFIXES)
    n = 0
    with gzip.open(out, "wt", encoding="utf-8") as f:
        for key in client.scan_iter(match=f"{prefix}:*", count=500):
            name = key.decode()
            if name.startswith(skip):
                continue
            payload = client.dump(key)
            if payload is None:                                  # expiró durante el recorrido
                continue
            ttl = client.pttl(key)
            f.write(json.dumps({"key": name, "pttl": max(ttl, 0), "dump": base64.b64encode(payload).decode()}) + "\n")
            n += 1
    return n


def redis_import(url: str, src: Path) -> int:
    client = make_redis(url)
    n = 0
    with gzip.open(src, "rt", encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            client.restore(rec["key"], rec["pttl"], base64.b64decode(rec["dump"]), replace=True)
            n += 1
    return n


def _redis_version(url: str) -> str:
    try:
        return make_redis(url).info("server").get("redis_version", "?")
    except Exception:  # noqa: BLE001 - informativo; algunos entornos no exponen INFO
        return "?"


def redis_count(src: Path) -> int:
    with gzip.open(src, "rt", encoding="utf-8") as f:
        return sum(1 for _ in f)


# -------------------------------------------------------------- destino remoto
def upload_remote(zip_path: Path) -> str | None:
    bucket, rclone = os.getenv("BACKUP_S3_BUCKET"), os.getenv("BACKUP_RCLONE_REMOTE")
    if bucket:
        try:
            import boto3
        except ImportError:
            sys.exit("Para subir a S3/R2/B2 instala boto3:  pip install boto3")
        s3 = boto3.client("s3", endpoint_url=os.getenv("BACKUP_S3_ENDPOINT") or None,
                          aws_access_key_id=os.getenv("BACKUP_S3_ACCESS_KEY"),
                          aws_secret_access_key=os.getenv("BACKUP_S3_SECRET_KEY"))
        key = os.getenv("BACKUP_S3_PREFIX", "ticketflash/") + zip_path.name
        s3.upload_file(str(zip_path), bucket, key, ExtraArgs={"ServerSideEncryption": "AES256"}
                       if not os.getenv("BACKUP_S3_ENDPOINT") else {})
        keep = int(os.getenv("BACKUP_KEEP", "14"))
        objs = sorted(s3.list_objects_v2(Bucket=bucket, Prefix=os.getenv("BACKUP_S3_PREFIX", "ticketflash/"))
                      .get("Contents", []), key=lambda o: o["LastModified"], reverse=True)
        for old in objs[keep:]:
            s3.delete_object(Bucket=bucket, Key=old["Key"])
            log(f"Retención remota: eliminado {old['Key']}")
        return f"s3://{bucket}/{key}"
    if rclone:
        exe = shutil.which("rclone")
        if not exe:
            sys.exit("rclone no está instalado (https://rclone.org/install/)")
        run([exe, "copy", str(zip_path), rclone], dict(os.environ))
        run([exe, "delete", rclone, "--min-age", f"{int(os.getenv('BACKUP_KEEP', '14'))}d"], dict(os.environ))
        return f"{rclone}/{zip_path.name}"
    return None


# ------------------------------------------------------------------- comandos
def cmd_backup(args) -> Path:
    pg_url = args.pg_url or os.getenv("BACKUP_DATABASE_URL") or os.getenv("DATABASE_URL")
    redis_url = args.redis_url or os.getenv("BACKUP_REDIS_URL") or os.getenv("REDIS_URL")
    if not pg_url or not redis_url:
        sys.exit("Faltan BACKUP_DATABASE_URL y BACKUP_REDIS_URL (ver .env.example)")
    prefix = os.getenv("KEY_PREFIX", "tf")
    out_dir = Path(args.out or os.getenv("BACKUP_DIR", "backups"))
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    name = f"ticketflash-{stamp}"

    with tempfile.TemporaryDirectory(prefix=name) as tmp:
        tmp = Path(tmp)
        log("PostgreSQL: pg_dump (formato custom, esquema ticketflash)...")
        a, env = pg_args(pg_url)
        run([find_pg_tool("pg_dump"), *a, "--format=custom", "--schema=ticketflash", "--no-owner",
             "--no-acl", "--file", str(tmp / "postgres.dump")], env)
        log("Redis: exportando claves con DUMP + TTL...")
        keys_n = redis_export(redis_url, prefix, tmp / "redis.jsonl.gz")

        with psycopg.connect(pg_url, connect_timeout=15) as conn:
            pg_version = conn.execute("SHOW server_version").fetchone()[0]
        redis_version = _redis_version(redis_url)
        manifest = {
            "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "postgres": {"server_version": pg_version, "rows": pg_counts(pg_url)},
            "redis": {"server_version": redis_version, "keys": keys_n, "prefix": prefix,
                      "excluded": list(SKIP_PREFIXES)},
            "files": {f.name: sha256(f) for f in sorted(tmp.iterdir())},
        }
        (tmp / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

        zip_path = out_dir / f"{name}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(tmp.iterdir()):
                z.write(f, f.name)
    log(f"Respaldo creado: {zip_path} ({zip_path.stat().st_size / 1024:.1f} KB)  "
        f"filas PG={sum(manifest['postgres']['rows'].values())}  claves Redis={keys_n}")

    remote = upload_remote(zip_path)
    log(f"Copia remota: {remote}" if remote else
        "Sin destino remoto configurado (BACKUP_S3_BUCKET o BACKUP_RCLONE_REMOTE): solo copia local.")

    keep = int(os.getenv("BACKUP_KEEP", "14"))
    for old in sorted(out_dir.glob("ticketflash-*.zip"))[:-keep]:
        old.unlink()
        log(f"Retención local: eliminado {old.name}")
    return zip_path


def _extract(zip_path: Path, dest: Path) -> dict:
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest)
    manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    for fname, expected in manifest["files"].items():
        if sha256(dest / fname) != expected:
            sys.exit(f"INTEGRIDAD: el archivo {fname} está corrupto o fue modificado (SHA-256 distinto)")
    return manifest


def _pg_restore(dump: Path, admin_url: str, dbname: str) -> None:
    """Restaura en una base ya creada, con el rol dueño (sin owners ni ACL originales)."""
    a, env = pg_args(admin_url)
    a[a.index("-d") + 1] = dbname
    run([find_pg_tool("pg_restore"), *a, "--no-owner", "--no-acl", "--exit-on-error", str(dump)], env)


def cmd_verify(args) -> None:
    """Prueba de restauración: crea una BD temporal, restaura y compara con el manifiesto."""
    with tempfile.TemporaryDirectory() as tmp:
        manifest = _extract(Path(args.zip), Path(tmp))
        log("Integridad SHA-256 correcta")
        scratch = f"tf_verify_{datetime.now().strftime('%H%M%S')}"
        u = urlsplit(args.admin_url)
        admin_postgres = urlunsplit((u.scheme, u.netloc, "/postgres", u.query, ""))
        with psycopg.connect(admin_postgres, autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{scratch}"')
        try:
            scratch_url = urlunsplit((u.scheme, u.netloc, f"/{scratch}", u.query, ""))
            _pg_restore(Path(tmp) / "postgres.dump", args.admin_url, scratch)
            restored = pg_counts(scratch_url)
        finally:
            with psycopg.connect(admin_postgres, autocommit=True) as conn:
                conn.execute(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)')
        expected = manifest["postgres"]["rows"]
        log(f"PostgreSQL restaurado en BD temporal: {restored}")
        if restored != expected:
            sys.exit(f"VERIFICACIÓN FALLIDA: esperado {expected}")
        n = redis_count(Path(tmp) / "redis.jsonl.gz")
        if n != manifest["redis"]["keys"]:
            sys.exit("VERIFICACIÓN FALLIDA: número de claves de Redis distinto")
        log(f"Redis: {n} claves legibles en el archivo")
    log("VERIFICACIÓN CORRECTA: el respaldo se puede restaurar.")


def cmd_restore(args) -> None:
    if not args.yes:
        sys.exit("Restaurar SOBRESCRIBE datos. Repite el comando con --yes si estás seguro.")
    with tempfile.TemporaryDirectory() as tmp:
        manifest = _extract(Path(args.zip), Path(tmp))
        if args.pg_admin_url:
            u = urlsplit(args.pg_admin_url)
            dbname = (u.path or "/postgres").lstrip("/")
            log(f"PostgreSQL: restaurando en '{dbname}' (se reemplaza el esquema ticketflash)...")
            with psycopg.connect(args.pg_admin_url, autocommit=True) as conn:
                conn.execute("DROP SCHEMA IF EXISTS ticketflash CASCADE")   # el dump recrea el esquema
            _pg_restore(Path(tmp) / "postgres.dump", args.pg_admin_url, dbname)
            from scripts.init_db import SQL_DIR
            roles_sql = (SQL_DIR / "02_roles_privileges.sql").read_text(encoding="utf-8")
            for placeholder, env_name in (("CAMBIA_ESTA_CLAVE_APP", "TF_APP_PASSWORD"),
                                          ("CAMBIA_ESTA_CLAVE_ANALYST", "TF_ANALYST_PASSWORD"),
                                          ("CAMBIA_ESTA_CLAVE_BACKUP", "TF_BACKUP_PASSWORD")):
                roles_sql = roles_sql.replace(placeholder, os.getenv(env_name, "cambia-esta-clave-tras-restaurar"))
            with psycopg.connect(args.pg_admin_url, autocommit=True) as conn:
                conn.execute(roles_sql)
            log(f"PostgreSQL restaurado: {pg_counts(args.pg_admin_url)} (roles y privilegios reaplicados)")
        if args.redis_url:
            log("Redis: restaurando claves (RESTORE ... REPLACE, con TTL)...")
            n = redis_import(args.redis_url, Path(tmp) / "redis.jsonl.gz")
            log(f"Redis restaurado: {n} claves")
    log("Restauración completada. Ejecuta:  python -m app.cli reconcile   (recalcula stock y ranking)")


def cmd_list(_args) -> None:
    out_dir = Path(os.getenv("BACKUP_DIR", "backups"))
    for z in sorted(out_dir.glob("ticketflash-*.zip")):
        print(f"{z.name}  {z.stat().st_size / 1024:8.1f} KB")
    if not any(out_dir.glob("ticketflash-*.zip")):
        print("No hay respaldos locales.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("backup", help="Crear respaldo")
    p.add_argument("--pg-url"); p.add_argument("--redis-url"); p.add_argument("--out")
    p.set_defaults(fn=cmd_backup)
    p = sub.add_parser("verify", help="Probar que un respaldo se puede restaurar")
    p.add_argument("zip"); p.add_argument("--admin-url", required=True, help="Conexión del dueño de PostgreSQL")
    p.set_defaults(fn=cmd_verify)
    p = sub.add_parser("restore", help="Restaurar un respaldo")
    p.add_argument("zip"); p.add_argument("--pg-admin-url"); p.add_argument("--redis-url")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(fn=cmd_restore)
    sub.add_parser("list", help="Listar respaldos locales").set_defaults(fn=cmd_list)
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
