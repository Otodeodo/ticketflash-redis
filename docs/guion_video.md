# Guion del video de funcionalidad (25 puntos)

Duración sugerida: **10–12 minutos**. Orden = orden de la rúbrica. Deja abiertas, antes de grabar:
la app (`https://ticketflash-xa9o.onrender.com`, ábrela 1 min antes para despertarla), el **SQL Editor de Neon**
(o `psql`), **RedisInsight / consola de Redis Cloud**, y una terminal en la carpeta del proyecto con el entorno
activado. Ten la contraseña de demo a mano (`SEED_PASSWORD`).

> Consejo: usa un cliente (`ana@example.com`) en una ventana normal y el administrador
> (`admin@example.com`) en una ventana privada, para alternar sin cerrar sesión.

## 0. Presentación (30 s)

* Qué es TicketFlash y por qué **dos bases**: PostgreSQL = lo que debe ser exacto y durar; Redis = lo rápido y efímero.
* Muestra el diagrama de arquitectura (Figura 1 del PDF) y el footer de la app con los dos indicadores verdes.

## 1. Funcionamiento de la aplicación y cambios en cada base (3 min)

| Paso en la app | Qué mostrar en PostgreSQL (SQL Editor) | Qué mostrar en Redis |
|---|---|---|
| Registrar una cuenta nueva y entrar | `SELECT id, name, email, role, left(password_hash, 18) FROM ticketflash.users ORDER BY id DESC LIMIT 1;` → la contraseña es un hash `scrypt$…` | `KEYS tf:session:*` y `TTL tf:session:<token>` (~1800) — la sesión vive en Redis |
| Abrir un evento y **reservar** 2 entradas | `SELECT id, capacity, sold FROM ticketflash.events WHERE id = 2;` → `sold` **no cambia** todavía | `GET tf:event:2:stock` baja en 2; `HGETALL tf:hold:<id>`; `TTL tf:hold:<id>` (~600) |
| **Confirmar** la compra | `SELECT * FROM ticketflash.orders ORDER BY id DESC LIMIT 1;` y `SELECT sold FROM ticketflash.events WHERE id = 2;` → `sold` sube | `ZSCORE tf:ranking:sales 2` sube; `LRANGE tf:activity 0 0`; la clave `tf:hold:<id>` ya no existe |
| **Cancelar** la orden | La orden pasa a `CANCELLED`, `sold` baja | El stock de Redis vuelve a subir |
| (Opcional) Dejar **expirar** una reserva | — | La clave desaparece sola y el stock se restituye |

Cierra la sección con el panel **Consistencia** (admin): todos los eventos en **OK**.

## 2. Modelo de seguridad (2,5 min)

1. **Roles de PostgreSQL** (SQL Editor con el usuario dueño): `\du` o
   `SELECT rolname FROM pg_roles WHERE rolname LIKE 'tf_%';` y muestra `sql/02_roles_privileges.sql`.
2. **Pruebas de lo que NO se puede hacer**, conectado como `tf_app` (cadena `DATABASE_URL`):
   ```sql
   DELETE FROM ticketflash.orders;                       -- permission denied
   INSERT INTO ticketflash.audit_log(action, entity) VALUES ('x','y');   -- permission denied
   UPDATE ticketflash.orders SET unit_price = 0.01;      -- permission denied (columna)
   ```
   Y como `tf_analyst`: `SELECT password_hash FROM ticketflash.users;` → permission denied.
3. **Redis ACL:** `redis-cli --user tf_app ... SET otra:clave 1` → NOPERM (solo `tf:*`);
   `FLUSHALL` → NOPERM; con `tf_readonly`: `SET tf:x 1` → NOPERM.
4. **Aplicación:** intenta entrar 5 veces con mala contraseña → bloqueo 429 (muestra `GET tf:ratelimit:login:<correo>` y su TTL).
   Como cliente, abre `/admin/sales` (403) y prueba crear un evento (no aparece el botón; la API responde 403).
5. **Auditoría inalterable:** panel Auditoría (admin) y `SELECT * FROM ticketflash.audit_log ORDER BY id DESC LIMIT 5;`.

## 3. Administración de la concurrencia (2,5 min)

1. Terminal: `python scripts/concurrency_demo.py --buyers 60 --capacity 20`
   * Explica la salida: 20 compras, 40 "sin stock", **sin sobreventa**, PostgreSQL ↔ Redis consistentes.
2. Parte 2 de la demo: la 2.ª transacción **espera 3 s** por el bloqueo de fila (`FOR UPDATE`/`UPDATE`).
3. Parte 3: **bloqueo optimista** (`version`): el administrador B es rechazado. Repítelo en la app con dos ventanas de admin editando el mismo evento.
4. Muestra el script Lua (`app/db.py`) y la restricción `CHECK (sold <= capacity)` en `sql/01_schema.sql`.
5. `python -m pytest tests/test_concurrency.py -q` → pruebas verdes.

## 4. Conexiones remotas (1,5 min)

* Muestra `.env` **sin revelar las claves** (o `.env.example`): `DATABASE_URL` con `sslmode=require` y `REDIS_URL` (`rediss://`).
* Panel de Neon y consola de Redis Cloud: las bases están en la nube, no en tu equipo.
* Ejecuta desde tu PC `python -m app.cli status` (respuesta con `pg_version` y `redis_version` remotos) y recarga la app desplegada en **Render**:
  la misma app, desde otro servidor, ve los mismos datos.
* (Opcional) Desde otro equipo/contenedor: `psql "<DATABASE_URL>" -c "SELECT count(*) FROM ticketflash.orders;"`.

## 5. Programa de respaldo (2 min)

1. `python scripts/backup.py backup` → muestra los mensajes (pg_dump, claves de Redis, `.zip`, copia remota).
2. Abre el `.zip`: `postgres.dump`, `redis.jsonl.gz`, `manifest.json` (SHA-256, conteos). Menciona que **no** incluye sesiones.
3. Muestra la copia **remota** (carpeta de Drive / bucket).
4. `python scripts/backup.py verify backups/<zip> --admin-url "<ADMIN_DATABASE_URL>"` → "VERIFICACIÓN CORRECTA".
5. **Desastre simulado (recomendado en una base de pruebas):** borra datos, ejecuta `restore … --yes`, y
   `python -m app.cli reconcile`; muestra que las órdenes y el ranking vuelven y que el panel Consistencia queda en OK.
6. Muestra `scripts/schedule_backup.ps1` y la tarea creada en el Programador de tareas.

## Cierre (30 s)

Resumen: dos bases con responsabilidades distintas, transacciones ACID + saga, concurrencia en dos capas,
seguridad por mínimo privilegio y respaldo remoto verificable.
