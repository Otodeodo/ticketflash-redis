# TicketFlash — Venta de entradas con reservas temporales sobre Redis Cloud

Aplicación web funcional para la asignatura de bases de datos NoSQL. Modelo
**Clave-Valor** con **Redis Cloud** (servicio administrado), desarrollada en
**Python** con el SDK oficial `redis-py`. Consta de una interfaz web
(HTML/CSS/JS), una API REST (FastAPI) y una CLI de demostración, todas sobre
la misma capa de repositorios que habla con Redis.

* **Aplicación en línea:** <https://ticketflash-xa9o.onrender.com>
* **API (Swagger):** <https://ticketflash-xa9o.onrender.com/docs>
* **Repositorio:** <https://github.com/Otodeodo/ticketflash-redis>

Caso de uso: una taquilla en línea donde miles de usuarios compiten por las
mismas entradas. El sistema necesita:

* **Stock en tiempo real** con decrementos atómicos (sin sobreventa).
* **Reservas temporales** que bloquean entradas 10 minutos y expiran solas (TTL).
* **Sesiones de usuario** con expiración deslizante (TTL renovado en cada acceso).
* **Ranking de ventas** instantáneo (Sorted Set).
* **Caché** de resúmenes con expiración corta y **visitantes únicos** (HyperLogLog).

## Estructura del repositorio

```
ticketflash-redis/
├── app/
│   ├── config.py          # Configuración desde variables de entorno (.env)
│   ├── keys.py            # Convención de nombres de claves (namespaces)
│   ├── db.py              # Cliente redis-py + scripts Lua atómicos
│   ├── models.py          # Modelos Pydantic de la API
│   ├── api.py             # API REST (FastAPI) + sirve la interfaz web en /
│   ├── cli.py             # CLI para la demostración en vivo
│   ├── static/            # Interfaz web: index.html, app.css, app.js
│   └── repositories/      # Acceso a datos por entidad
│       ├── events.py      # HASH + STRING (stock) + SET (índices) + HLL + caché
│       ├── users.py       # HASH + SET
│       ├── sessions.py    # HASH con TTL deslizante
│       ├── holds.py       # HASH con TTL + ZSET de expiración (Lua)
│       ├── orders.py      # HASH + LIST + ZSET ranking (Lua)
│       └── analytics.py   # ZREVRANGE, LRANGE, SCAN
├── scripts/seed.py        # Script de datos iniciales (seeding)
├── tests/                 # Pruebas con fakeredis (no requieren la nube)
├── docs/                  # Documento de diseño (Markdown + PDF) y capturas
├── .env.example           # Plantilla de credenciales
├── render.yaml            # Despliegue de la web en Render (hosting gratuito)
├── requirements.txt       # Dependencias de ejecución
├── requirements-dev.txt   # + pruebas y generación del PDF
└── README.md
```

## Requisitos

* Python 3.11 o superior.
* Una base de datos en **Redis Cloud** (plan gratuito de 30 MB): <https://redis.io/try-free/>
* Dependencias de `requirements.txt` (redis, fastapi, uvicorn, python-dotenv, pydantic).

## Instalación

```bash
git clone <url-del-repositorio>
cd ticketflash-redis
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate
pip install -r requirements-dev.txt   # o requirements.txt si solo vas a ejecutar la app
```

### Crear la base de datos en Redis Cloud

1. Entra en <https://cloud.redis.io> y crea una cuenta (no pide tarjeta).
2. "New database" → plan **Free** → región más cercana → "Create".
3. En la pestaña de la base pulsa **Connect** y copia la URL de conexión
   (`redis://default:<password>@<host>:<port>`).

### Configurar credenciales

Las credenciales se leen **solo** de variables de entorno; nunca van en el
código ni en el repositorio (`.env` está en `.gitignore`).

```bash
cp .env.example .env
# Edita .env y pega tu REDIS_URL
```

Variables disponibles:

| Variable | Descripción | Por defecto |
|---|---|---|
| `REDIS_URL` | URL completa `redis://user:pass@host:port` (prioritaria) | — |
| `REDIS_HOST`, `REDIS_PORT`, `REDIS_USERNAME`, `REDIS_PASSWORD`, `REDIS_SSL` | Alternativa a `REDIS_URL` | `localhost`, `6379`, `default`, —, `false` |
| `KEY_PREFIX` | Prefijo de todas las claves | `tf` |
| `SESSION_TTL_SECONDS` | TTL deslizante de sesiones | `1800` |
| `HOLD_TTL_SECONDS` | TTL fijo de reservas temporales | `600` |
| `CACHE_TTL_SECONDS` | TTL de la caché de resúmenes | `60` |
| `ACTIVITY_MAX` | Longitud máxima del feed de actividad | `100` |

## Ejecución

### 1. Poblar la base de datos (seeding)

```bash
python scripts/seed.py --reset
```

Crea 5 usuarios, 7 eventos con stock, 7 órdenes confirmadas (para que el
ranking tenga datos) y una reserva temporal activa. `--reset` borra antes las
claves del namespace `tf:*` con `SCAN` + `DEL` (nunca `FLUSHDB`).

### 2. Aplicación web (demostración en vivo)

```bash
uvicorn app.api:app --reload
```

Abre <http://127.0.0.1:8000/>, o la versión publicada en Render. El diseño se inspira en
plataformas de entradas como DICE y Fever: tipografía editorial, chips de categoría,
carrusel "Top" y tarjetas tipo póster. La interfaz permite:

* **Entrar** eligiendo o creando un perfil. El panel "Mis entradas" muestra el TTL
  deslizante de la sesión en Redis.
* Explorar el catálogo con **buscador**, **chips de categoría** (índice SET) y **stock en
  tiempo real**, con avisos de "Últimas" y "Agotado".
* Ver el **Top ventas** (Sorted Set), un **ticker de actividad en directo** (List) y las
  estadísticas de Redis: eventos, vendidas, disponibles y número de claves.
* Abrir un evento, elegir cantidad y **reservar**. La reserva aparece en "Mis entradas" con
  una **cuenta atrás circular** de 10 minutos. Si no se confirma, expira y el stock vuelve solo.
* **Confirmar la compra**, **soltar** una reserva o **cancelar** una orden. Las órdenes se
  muestran como tickets con talón.
* **Crear, editar, añadir stock y eliminar eventos** (CRUD completo) desde el botón ＋ de la
  cabecera y desde "Gestión del evento" en el detalle.

### 3. CLI (alternativa en terminal)

```bash
python -m app.cli status                       # conexión + claves por tipo
python -m app.cli events                       # listar eventos y stock
python -m app.cli events --category deporte    # índice secundario (SET)
python -m app.cli login 1                      # sesión con TTL deslizante
python -m app.cli hold --user 1 --event 2 --qty 2   # reserva temporal (Lua atómico)
python -m app.cli holds                        # reservas pendientes + segundos restantes
python -m app.cli confirm <hold_id>            # reserva -> orden (Lua atómico)
python -m app.cli ranking                      # top ventas (ZSET)
python -m app.cli activity                     # feed reciente (LIST)
python -m app.cli reclaim                      # liberar reservas expiradas
python -m app.cli demo                         # flujo completo automático
```

### 4. API REST

Con el servidor en marcha, <http://127.0.0.1:8000/docs> muestra Swagger UI.
Endpoints principales (los usa la interfaz web):

| Método | Ruta | Operación Redis |
|---|---|---|
| `POST /users` · `GET /users/{id}` · `DELETE /users/{id}` | CRUD usuarios | HSET / HGETALL / DEL + SADD |
| `POST /auth/login` · `GET /auth/me` · `POST /auth/logout` | Sesión con TTL deslizante | HSET + EXPIRE / TTL / DEL |
| `POST /events` · `GET /events?category=` · `PATCH` · `DELETE` | CRUD eventos + índice | HSET, SMEMBERS, pipeline MULTI/EXEC |
| `GET /events/{id}/summary` | Caché cache-aside | GET / SET EX |
| `POST /holds` | Reserva temporal | Script Lua: GET+DECRBY+HSET+EXPIRE+ZADD |
| `GET /holds/pending` · `POST /holds/reclaim` | Expiración perezosa | ZRANGEBYSCORE + Lua |
| `POST /holds/{id}/confirm` | Reserva → orden | Script Lua: DEL+HSET+LPUSH+ZINCRBY+LTRIM |
| `GET /ranking` | Top eventos | ZREVRANGE WITHSCORES |
| `GET /activity` | Feed reciente | LRANGE |
| `GET /stats/keys` | Claves por tipo | SCAN + TYPE |

Las rutas que modifican reservas u órdenes requieren el encabezado
`X-Session-Token` obtenido en `/auth/login`.

### 5. Despliegue en la nube (Render)

La aplicación está publicada en <https://ticketflash-xa9o.onrender.com>. El archivo `render.yaml` define el servicio,
así que para desplegar una copia propia:

1. Sube el repositorio a GitHub.
2. Abre `https://render.com/deploy?repo=<url-del-repositorio>` e inicia sesión.
3. Pega tu URL de Redis Cloud en la variable `REDIS_URL` y pulsa **Deploy Blueprint**.

Tras un `git push` a `main`, despliega la versión nueva desde el panel de Render con
**Manual Deploy → Deploy latest commit**. Si conectas tu cuenta de GitHub a Render, el
despliegue pasa a ser automático en cada push. En el plan gratuito el
servicio se suspende tras 15 minutos sin tráfico y tarda unos 30 segundos en despertar.

### 6. Pruebas

```bash
python -m pytest -q
```

Las pruebas usan `fakeredis` (con soporte Lua), por lo que no consumen la
instancia en la nube.

## Decisiones de diseño (resumen)

* **Namespaces**: `tf:<entidad>:<id>[:<sub-recurso>]`; ver `app/keys.py`.
* **Atomicidad**: las operaciones multi-clave críticas (reservar, confirmar,
  liberar) son scripts Lua ejecutados con `EVALSHA`; el resto usa pipelines
  con `MULTI/EXEC`.
* **TTL**: sesiones (deslizante), reservas (fijo) y caché (corto). Como una
  clave expirada no puede consultarse, el ZSET `tf:holds:expiring` guarda
  `hold:event:user:qty` con score = instante de expiración para devolver el stock.
* **Persistencia**: los datos de negocio (eventos, usuarios, órdenes) no tienen TTL.
  El plan Free de Redis Cloud no activa persistencia en disco; en Essentials se
  habilita AOF cada segundo sin cambiar código.

El documento completo de diseño y justificación está en `docs/`.
