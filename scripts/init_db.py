"""Crea el esquema relacional y los roles en PostgreSQL.

Uso (con el usuario DUEÑO de la base: neondb_owner en Neon, postgres en local):

    python scripts/init_db.py --admin-url "postgresql://owner:pwd@host/db?sslmode=require" \
        --app-password "..." --analyst-password "..." --backup-password "..."

Las contraseñas también pueden venir de las variables TF_APP_PASSWORD,
TF_ANALYST_PASSWORD y TF_BACKUP_PASSWORD; si no se indican se generan al azar y
se imprimen UNA vez (cópialas a tu .env / gestor de secretos).

Equivale a ejecutar sql/01_schema.sql y sql/02_roles_privileges.sql con psql,
pero sustituyendo los marcadores CAMBIA_ESTA_CLAVE_* por las contraseñas reales.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = ROOT / "sql"


def init_database(admin_url: str, *, app_password: str, analyst_password: str,
                  backup_password: str, sql_dir: Path = SQL_DIR) -> None:
    """Ejecuta 01_schema.sql y 02_roles_privileges.sql en una sola transacción."""
    for pwd in (app_password, analyst_password, backup_password):
        if "'" in pwd:
            raise ValueError("Las contraseñas no pueden contener comillas simples")
    schema = (sql_dir / "01_schema.sql").read_text(encoding="utf-8")
    roles = (sql_dir / "02_roles_privileges.sql").read_text(encoding="utf-8")
    roles = (roles.replace("CAMBIA_ESTA_CLAVE_APP", app_password)
                  .replace("CAMBIA_ESTA_CLAVE_ANALYST", analyst_password)
                  .replace("CAMBIA_ESTA_CLAVE_BACKUP", backup_password))
    with psycopg.connect(admin_url, autocommit=False) as conn:
        conn.execute(schema)
        conn.execute(roles)
        # Si los roles ya existían, las contraseñas del script no se aplican: forzarlas.
        for role, pwd in (("tf_app", app_password), ("tf_analyst", analyst_password),
                          ("tf_backup", backup_password)):
            conn.execute(f"ALTER ROLE {role} PASSWORD '{pwd}'")
        conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--admin-url", default=os.getenv("ADMIN_DATABASE_URL"),
                        help="Conexión del dueño de la base (o variable ADMIN_DATABASE_URL)")
    parser.add_argument("--app-password", default=os.getenv("TF_APP_PASSWORD"))
    parser.add_argument("--analyst-password", default=os.getenv("TF_ANALYST_PASSWORD"))
    parser.add_argument("--backup-password", default=os.getenv("TF_BACKUP_PASSWORD"))
    args = parser.parse_args()
    if not args.admin_url:
        parser.error("Falta --admin-url (o ADMIN_DATABASE_URL)")

    generated = {}
    for name in ("app_password", "analyst_password", "backup_password"):
        if not getattr(args, name):
            setattr(args, name, secrets.token_urlsafe(18))
            generated[name] = getattr(args, name)

    init_database(args.admin_url, app_password=args.app_password,
                  analyst_password=args.analyst_password, backup_password=args.backup_password)
    print("Esquema y roles creados en PostgreSQL.")
    if generated:
        print("Contraseñas generadas (guárdalas ahora, no se vuelven a mostrar):")
        for name, value in generated.items():
            print(f"  {name}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
