# TicketFlash — Venta de entradas con PostgreSQL (relacional) + Redis (no relacional)

Proyecto final de **Bases de Datos Avanzadas**. Sistema de venta de entradas con reservas
temporales que usa **dos bases de datos con responsabilidades distintas**, desarrollado en
**Python** (FastAPI + psycopg 3 + redis-py) con interfaz web y CLI.

| | PostgreSQL (Neon) | Redis (Redis Cloud) |
|---|---|---|
| Guarda | usuarios, eventos, órdenes, auditoría — lo que debe sobrevivir y ser exacto | stock en caliente, reservas con TTL, sesiones, ranking, caché — lo rápido y efímero |
| Garantías | ACID, `CHECK`/`UNIQUE`/`FOREIGN KEY`, bloqueo de filas, triggers, roles | scripts Lua atómicos, TTL nativo, ACL |
| Si se pierde | se recupera del respaldo | se **reconstruye** desde PostgreSQL (`reconcile`) |

* **Aplicación en línea:** <https://ticketflash-xa9o.onrender.com> · **API (Swagger):** `/docs`
* **Repositorio:** <https://github.com/Otodeodo/ticketflash-redis>
* **Documento de entrega (PDF):** [`docs/proyecto_final.pdf`](docs/proyecto_final.pdf) · guion del video: [`docs/guion_video.md`](docs/guion_video.md)

## Correspondencia con la rúbrica

| Entregable | Dónde está |
|---|---|
| Documentación y diseño (PDF) | `docs/proyecto_final.pdf` (fuente: `docs/proyecto_final.md`) |
| Creación de la BD relacional (`.sql`) | `sql/01_schema.sql` (+ `sql/03_queries_eficientes.sql`) |
| Creación de la BD no relacional (`.redis`) | `redis/00_keyspace.redis` (un ejemplo de cada estructura), `app/keys.py` (modelo de claves) y `scripts/seed.py` (datos) |
| Usuarios, roles y privilegios | `sql/02_roles_privileges.sql` (PostgreSQL) · `redis/01_users_acl.redis` (Redis ACL) |
| Aplicativo de software | `app/` (API, interfaz web, CLI, repositorios) |
| Transacciones | `app/pg.py`, `app/repositories/orders.py` (saga + ACID) |
| Concurrencia | `app/db.py` (Lua), `sql/01_schema.sql`, `tests/test_concurrency.py`, `scripts/concurrency_demo.py` |
| Conexión remota | `DATABASE_URL` / `REDIS_URL` por TLS hacia Neon y Redis Cloud |
| Seguridad | roles SQL, ACL Redis, scrypt, bloqueo de login, autorización por rol, auditoría |
| Proceso y software de respaldo | `scripts/backup.py`, `scripts/schedule_backup.ps1` |
| Consultas eficientes | índices en `sql/01_schema.sql`, evidencia `EXPLAIN` en el PDF (sec. 4.7) |

## Estructura del repositorio

```
ticketflash-redis/
├── sql/
│   ├── 01_schema.sql              # Tablas, restricciones, índices, vistas, triggers de auditoría
│   ├── 02_roles_privileges.sql    # Usuarios, roles y privilegios (mínimo privilegio)
│   └── 03_queries_eficientes.sql  # Consultas con EXPLAIN ANALYZE
├── redis/
│   ├── 00_keyspace.redis          # Estructuras del modelo de claves (ejemplos ejecutables)
│   └── 01_users_acl.redis         # Usuarios y ACL de Redis
├── app/
│   ├── config.py · keys.py        # Configuración (.env) y convención de claves de Redis
│   ├── db.py                      # Redis + scripts Lua atómicos
│   ├── pg.py                      # PostgreSQL: pool, transacciones, reintentos
│   ├── stores.py                  # Contenedor { postgres, redis }
│   ├── security.py                # Hash de contraseñas (scrypt)
│   ├── models.py · api.py · cli.py
│   ├── static/                    # Interfaz web (HTML/CSS/JS) con panel de administración
│   └── repositories/              # users, events, holds, orders, sessions, analytics, maintenance
├── scripts/
│   ├── init_db.py                 # Crea esquema + roles en PostgreSQL
│   ├── seed.py                    # Datos de demostración en ambas bases (--bulk N para carga masiva)
│   ├── backup.py                  # backup | list | verify | restore (PostgreSQL + Redis)
│   ├── schedule_backup.ps1        # Respaldo diario (Programador de tareas de Windows)
│   ├── concurrency_demo.py        # Demostración de concurrencia para el video
│   └── dev_local.py               # Modo local sin nube (PostgreSQL embebido + Redis en memoria)
├── tests/                         # 49 pruebas contra un PostgreSQL REAL + fakeredis
├── docs/                          # proyecto_final.md/.pdf, guion_video.md, capturas
├── docker-compose.yml             # PostgreSQL 17 + Redis 7 (con ACL) para laboratorio local
├── .env.example · render.yaml · requirements*.txt
```

## Requisitos

* Python 3.12 (recomendado; las pruebas con PostgreSQL embebido requieren Python ≤ 3.13).
* Una base **PostgreSQL** (Neon gratis: <https://neon.tech>) y una **Redis Cloud** (plan gratis: <https://redis.io/try-free/>).
* Para respaldos: clientes de PostgreSQL (`pg_dump`, `pg_restore`) de la **misma versión mayor** que el servidor.

## Instalación

```bash
git clone https://github.com/Otodeodo/ticketflash-redis
cd ticketflash-redis
python -m venv .venv
.venv\Scripts\activate            # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env              # y completa tus conexiones (ver abajo)
```

### 1. Crear el esquema y los roles en PostgreSQL

Con el usuario **dueño** de la base (en Neon: `neondb_owner`; su URL en `ADMIN_DATABASE_URL`):

```bash
python scripts/init_db.py --admin-url "postgresql://neondb_owner:...@ep-xxxx.neon.tech/neondb?sslmode=require"
```

Crea el esquema `ticketflash`, los roles `tf_app` (aplicación), `tf_analyst` (reportes) y `tf_backup`
(respaldo) y **imprime una sola vez** las contraseñas generadas. Copia la de `tf_app` a `DATABASE_URL`
y la de `tf_backup` a `BACKUP_DATABASE_URL`. (Equivale a ejecutar `sql/01_schema.sql` y
`sql/02_roles_privileges.sql` con `psql`, cambiando antes las contraseñas `CAMBIA_ESTA_CLAVE_*`.)

### 2. Usuarios de Redis (opcional, recomendado)

```bash
redis-cli -u "redis://default:<ADMIN_PWD>@<host>:<port>" < redis/01_users_acl.redis
```

En **Redis Cloud** los mismos permisos se crean en *Data Access Control* (reglas, roles y usuarios).
Después usa en `REDIS_URL` el usuario `tf_app`.

### 3. Datos de demostración y arranque

```bash
python scripts/seed.py --reset --admin-url "<ADMIN_DATABASE_URL>"
uvicorn app.api:app --reload
```

Abre <http://127.0.0.1:8000/>. La semilla crea **1 administrador** (`admin@example.com`) y 5 clientes
(`ana@example.com`, …) con la contraseña de la variable `SEED_PASSWORD` (ver `scripts/seed.py`;
**cámbiala** si la base es accesible desde Internet).

### Variables de entorno principales

| Variable | Descripción |
|---|---|
| `DATABASE_URL` | PostgreSQL con el usuario `tf_app` (`...?sslmode=require` en Neon) |
| `ADMIN_DATABASE_URL` | Dueño de la base; solo para `init_db`/`seed --reset` (no se despliega) |
| `REDIS_URL` | `redis://usuario:clave@host:puerto` (o `rediss://` con TLS) |
| `SESSION_TTL_SECONDS` · `HOLD_TTL_SECONDS` · `CACHE_TTL_SECONDS` | TTL de sesión (1800), reserva (600) y caché (60) |
| `LOGIN_MAX_ATTEMPTS` · `LOGIN_BLOCK_SECONDS` | Bloqueo anti fuerza bruta (5 intentos / 300 s) |
| `BACKUP_*` | Conexiones de solo lectura y destino remoto del respaldo (ver `.env.example`) |

## Cómo funciona

* **Reservar** (Redis): un script Lua comprueba y descuenta el stock de forma atómica y crea una
  reserva con `EXPIRE 600`. Si no se confirma, expira y el stock vuelve solo.
* **Confirmar** (Redis + PostgreSQL, *saga*): se reclama la reserva en Redis (Lua), se abre una transacción
  ACID en PostgreSQL (`UPDATE events SET sold = sold + qty WHERE sold + qty <= capacity` + `INSERT INTO orders`)
  y, si falla, se **compensa** devolviendo el stock a Redis. Una orden existe solo si su transacción se confirmó.
* **Sin sobreventa en dos capas:** Lua en Redis y `UPDATE` condicional + `CHECK (sold <= capacity)` en PostgreSQL.
* **Concurrencia:** `FOR UPDATE` al cancelar, índice único `ux_orders_hold` (una reserva = una orden),
  bloqueo optimista con `events.version`, reintento automático ante deadlocks.
* **Seguridad:** contraseñas `scrypt`; bloqueo tras 5 intentos (contador en Redis); roles `customer`/`admin`;
  `tf_app` sin `DELETE` sobre `orders` ni acceso de escritura a `audit_log`; Redis ACL por usuario.
* **Auditoría:** triggers `SECURITY DEFINER` registran ventas, cancelaciones y cambios de precio.
* **Reconciliación:** `python -m app.cli reconcile` reconstruye stock y ranking de Redis desde PostgreSQL.

### Interfaz web

Catálogo con buscador y categorías, detalle con reserva, "Mis entradas" con cuenta atrás, y —para el rol
`admin`— botón ＋ (crear evento), edición con bloqueo optimista y **Panel de administración**
(ventas, órdenes, usuarios, auditoría y consistencia PostgreSQL ↔ Redis con botón de reconstrucción).

### CLI

```bash
python -m app.cli status          # conexiones, filas y claves
python -m app.cli events          # catálogo
python -m app.cli hold --user 2 --event 2 --qty 2
python -m app.cli confirm <id_reserva> --user 2
python -m app.cli sales           # ingresos (vista SQL)
python -m app.cli audit           # bitácora
python -m app.cli consistency     # PostgreSQL vs Redis
python -m app.cli reconcile       # reconstruye Redis
python -m app.cli demo            # flujo completo comentado
```

### API REST

Swagger en `/docs`. Las rutas de escritura requieren `X-Session-Token` (`POST /auth/login`); las de
gestión (`/events` POST/PATCH/DELETE, `/users`, `/orders`, `/admin/*`) requieren rol `admin`.
Códigos de error: 401 (credenciales/sesión), 403 (permiso), 404, 409 (conflicto: sin stock, versión
desfasada…), 429 (demasiados intentos), 503 (una base no disponible).

## Concurrencia: demostración

```bash
python scripts/concurrency_demo.py --buyers 60 --capacity 20
```

Lanza 60 compradores simultáneos por 20 entradas y muestra que se venden exactamente 20, que PostgreSQL y
Redis coinciden, cómo una transacción espera a otra por el bloqueo de fila y cómo el bloqueo optimista
rechaza una edición con versión vieja.

## Respaldo y restauración

```bash
python scripts/backup.py backup                      # pg_dump + DUMP de Redis + manifest SHA-256 -> .zip (+ remoto)
python scripts/backup.py list
python scripts/backup.py verify backups/ticketflash-....zip --admin-url "<ADMIN_DATABASE_URL>"   # prueba de restauración
python scripts/backup.py restore backups/ticketflash-....zip --pg-admin-url "<URL>" --redis-url "<URL>" --yes
powershell -ExecutionPolicy Bypass -File scripts\schedule_backup.ps1 -Hora 02:30                # programar diario
```

* Respalda PostgreSQL (esquema `ticketflash`, formato custom) y las claves `tf:*` de Redis con `DUMP`/`PTTL`
  (funciona en Redis Cloud). **No** respalda sesiones ni contadores de login.
* Usa credenciales de **solo lectura** (`tf_backup`).
* Destino remoto opcional: S3/Cloudflare R2/Backblaze B2 (`BACKUP_S3_*`, `pip install boto3`) o
  `rclone` (Google Drive, OneDrive…, `BACKUP_RCLONE_REMOTE`). Retención con `BACKUP_KEEP`.
* Tras restaurar: `python -m app.cli reconcile`.

## Entorno local alternativo

* **Docker:** `docker compose up -d` levanta PostgreSQL 17 y Redis 7 con los usuarios ACL; después `init_db`,
  `seed` y `uvicorn` apuntando a `localhost`.
* **Sin nube ni Docker:** `python scripts/dev_local.py` (PostgreSQL embebido + Redis en memoria + datos de demo).
  Solo para desarrollo.

## Despliegue en Render

`render.yaml` define el servicio. En el panel de Render añade como secretos `DATABASE_URL` (usuario `tf_app`)
y `REDIS_URL`. Tras cada `git push`: **Manual Deploy → Deploy latest commit** (o conecta GitHub para despliegue
automático). El plan gratuito suspende el servicio tras 15 min sin tráfico (~30 s en despertar).

## Pruebas

```bash
python -m pytest -q
```

49 pruebas: repositorios, concurrencia (hilos reales), seguridad de roles (intentos prohibidos deben fallar
con `InsufficientPrivilege`), API, y el ciclo completo de respaldo → verificación → desastre → restauración.
Usan un **PostgreSQL real y temporal** (`pgserver`) y `fakeredis`; no tocan la nube. Con
`TEST_ADMIN_DATABASE_URL` se pueden ejecutar contra otro PostgreSQL.

## Documento anterior (NoSQL)

`docs/diseno.pdf` es la entrega de la fase solo-Redis de este proyecto; se conserva como referencia.
