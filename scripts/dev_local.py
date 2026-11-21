"""Modo de desarrollo 100 % local, sin cuentas en la nube.

Levanta un PostgreSQL REAL embebido (paquete `pgserver`) con el esquema y los roles de
sql/, y un Redis en memoria (fakeredis, con scripts Lua), carga los datos de demostración
y arranca la web en http://127.0.0.1:8000.

    pip install -r requirements-dev.txt
    python scripts/dev_local.py            # datos persistentes en .devdata/
    python scripts/dev_local.py --fresh    # empezar de cero

Solo sirve para desarrollo y pruebas: los datos de Redis no sobreviven al reinicio.
Para el proyecto real usa Neon/PostgreSQL y Redis Cloud (ver README).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import fakeredis  # noqa: E402
import psycopg  # noqa: E402
import pgserver  # noqa: E402
import uvicorn  # noqa: E402

from scripts.init_db import init_database  # noqa: E402

PASSWORDS = {"app": "dev-app-pw", "analyst": "dev-analyst-pw", "backup": "dev-backup-pw"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fresh", action="store_true", help="Borrar .devdata y empezar de cero")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    data_dir = ROOT / ".devdata" / "pg"
    if args.fresh and data_dir.parent.exists():
        shutil.rmtree(data_dir.parent)
    data_dir.mkdir(parents=True, exist_ok=True)

    server = pgserver.get_server(data_dir, cleanup_mode="stop")
    base = urlsplit(server.get_uri())
    with psycopg.connect(server.get_uri(), autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname = 'ticketflash'").fetchone():
            conn.execute("CREATE DATABASE ticketflash")
    admin_url = urlunsplit((base.scheme, base.netloc, "/ticketflash", "", ""))
    app_url = urlunsplit((base.scheme, f"tf_app:{PASSWORDS['app']}@{base.hostname}:{base.port}", "/ticketflash", "", ""))
    init_database(admin_url, app_password=PASSWORDS["app"], analyst_password=PASSWORDS["analyst"],
                  backup_password=PASSWORDS["backup"])

    os.environ["DATABASE_URL"] = app_url
    os.environ["ADMIN_DATABASE_URL"] = admin_url
    print("DATABASE_URL (rol tf_app):", app_url)
    print("ADMIN_DATABASE_URL       :", admin_url)

    # Redis en memoria compartido por todo el proceso.
    import app.db as appdb
    fake = fakeredis.FakeRedis(decode_responses=True)
    # fakeredis no implementa INFO: se simula para que la pantalla de estado muestre algo útil.
    fake.info = lambda section=None, **_k: (
        {"redis_version": "7.2 (fakeredis)", "redis_mode": "standalone"} if section == "server"
        else {"used_memory_human": "1.0M"})
    appdb.make_client = lambda **_kw: fake

    from scripts import seed
    seed.seed(reset=False, admin_url=admin_url, bulk=0)

    uvicorn.run("app.api:app", host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
