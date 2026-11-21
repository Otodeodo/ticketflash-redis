"""API REST (FastAPI) de TicketFlash.

Ejecutar:  uvicorn app.api:app --reload
App web:   http://127.0.0.1:8000/       (interfaz de usuario)
Docs:      http://127.0.0.1:8000/docs   (Swagger UI)

Dos bases de datos:
  * PostgreSQL (relacional)    -> usuarios, eventos, órdenes, auditoría.
  * Redis      (no relacional) -> stock, reservas con TTL, sesiones, ranking, caché.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import psycopg
import redis as redis_lib
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.models import (EventIn, EventOut, EventUpdate, HoldIn, HoldOut, LoginIn, OrderOut,
                        RankingEntry, RegisterIn, SessionOut, UserOut)
from app.repositories import (AnalyticsRepository, EventRepository, HoldRepository,
                              MaintenanceService, OrderRepository, SessionRepository,
                              UserRepository)
from app.repositories.common import (ConflictError, ForbiddenError, InvalidCredentials,
                                     NotFoundError, TooManyAttempts)
from app.stores import Stores, get_stores

log = logging.getLogger("ticketflash")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Al arrancar, reconstruye en Redis lo que falte (stock, ranking) desde PostgreSQL."""
    try:
        report = MaintenanceService(get_stores()).reconcile(fix=True)
        log.info("Reconciliación inicial: %s", report)
    except Exception as exc:  # noqa: BLE001 - la app debe arrancar aunque una BD esté caída
        log.warning("No se pudo reconciliar al arrancar: %s", exc)
    yield


app = FastAPI(
    title="TicketFlash API",
    version=__version__,
    description="Venta de entradas con PostgreSQL (relacional) y Redis (no relacional).",
    lifespan=lifespan,
)


# --- Interfaz web (aplicación de usuario) -------------------------------------
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    """Sirve la aplicación web (app/static/index.html)."""
    return FileResponse(STATIC_DIR / "index.html")


# --- Manejo de errores -------------------------------------------------------
def _handler(status: int):
    async def handle(_: Request, exc: Exception):
        return JSONResponse(status_code=status, content={"detail": str(exc)})
    return handle


app.add_exception_handler(NotFoundError, _handler(404))
app.add_exception_handler(ConflictError, _handler(409))
app.add_exception_handler(ForbiddenError, _handler(403))
app.add_exception_handler(InvalidCredentials, _handler(401))
app.add_exception_handler(TooManyAttempts, _handler(429))


@app.exception_handler(psycopg.OperationalError)
async def _pg_down(_: Request, exc: Exception):
    return JSONResponse(status_code=503, content={"detail": "PostgreSQL no está disponible"})


@app.exception_handler(redis_lib.exceptions.ConnectionError)
async def _redis_down(_: Request, exc: Exception):
    return JSONResponse(status_code=503, content={"detail": "Redis no está disponible"})


# --- Dependencias ------------------------------------------------------------
def stores() -> Stores:
    return get_stores()


def current_auth(st: Stores = Depends(stores),
                 x_session_token: str = Header(..., alias="X-Session-Token")) -> dict:
    """Valida el token de sesión (Redis), renueva su TTL y devuelve {user_id, role, token}."""
    try:
        return SessionRepository(st).touch(x_session_token)
    except NotFoundError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def require_admin(auth: dict = Depends(current_auth)) -> dict:
    if auth["role"] != "admin":
        raise HTTPException(status_code=403, detail="Solo los administradores pueden hacer esto")
    return auth


def _self_or_admin(auth: dict, user_id: str) -> None:
    if auth["role"] != "admin" and auth["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="No puedes ver datos de otro usuario")


# --- Estado ------------------------------------------------------------------
@app.get("/health", tags=["estado"])
def health(st: Stores = Depends(stores)):
    return st.health()


@app.get("/stats/keys", tags=["estado"])
def key_overview(st: Stores = Depends(stores)):
    return AnalyticsRepository(st).key_overview()


@app.get("/stats/postgres", tags=["estado"])
def pg_overview(st: Stores = Depends(stores)):
    return st.pg.table_counts()


# --- Autenticación (usuarios en PostgreSQL, sesiones en Redis) ----------------
def _open_session(st: Stores, user: dict) -> dict:
    return {**SessionRepository(st).login(user), "user": user}


@app.post("/auth/register", response_model=SessionOut, status_code=201, tags=["sesiones"])
def register(body: RegisterIn, st: Stores = Depends(stores)):
    user = UserRepository(st).create(name=body.name, email=body.email, password=body.password)
    return _open_session(st, user)


@app.post("/auth/login", response_model=SessionOut, tags=["sesiones"])
def login(body: LoginIn, st: Stores = Depends(stores)):
    return _open_session(st, UserRepository(st).authenticate(body.email, body.password))


@app.get("/auth/me", tags=["sesiones"])
def me(st: Stores = Depends(stores), auth: dict = Depends(current_auth)):
    return {"user": UserRepository(st).get(auth["user_id"]),
            "session_ttl": SessionRepository(st).ttl(auth["token"])}


@app.post("/auth/logout", tags=["sesiones"])
def logout(st: Stores = Depends(stores), auth: dict = Depends(current_auth)):
    return {"logged_out": SessionRepository(st).logout(auth["token"])}


# --- Usuarios ----------------------------------------------------------------
@app.get("/users", response_model=list[UserOut], tags=["usuarios"])
def list_users(st: Stores = Depends(stores), _: dict = Depends(require_admin)):
    return UserRepository(st).list_all()


@app.get("/users/{user_id}", response_model=UserOut, tags=["usuarios"])
def get_user(user_id: str, st: Stores = Depends(stores), auth: dict = Depends(current_auth)):
    _self_or_admin(auth, user_id)
    return UserRepository(st).get(user_id)


@app.delete("/users/{user_id}", status_code=204, tags=["usuarios"])
def delete_user(user_id: str, st: Stores = Depends(stores), _: dict = Depends(require_admin)):
    UserRepository(st).delete(user_id)


# --- Eventos (lectura pública; escritura solo administradores) -----------------
@app.post("/events", response_model=EventOut, status_code=201, tags=["eventos"])
def create_event(body: EventIn, st: Stores = Depends(stores), _: dict = Depends(require_admin)):
    return EventRepository(st).create(**body.model_dump())


@app.get("/events", response_model=list[EventOut], tags=["eventos"])
def list_events(category: str | None = Query(default=None), st: Stores = Depends(stores)):
    repo = EventRepository(st)
    return repo.list_by_category(category) if category else repo.list_all()


@app.get("/events/categories", tags=["eventos"])
def list_categories(st: Stores = Depends(stores)):
    return EventRepository(st).categories()


@app.get("/events/{event_id}", response_model=EventOut, tags=["eventos"])
def get_event(event_id: str, request: Request, st: Stores = Depends(stores)):
    visitor = request.headers.get("X-Session-Token") or (request.client.host if request.client else None)
    return EventRepository(st).get(event_id, track_visitor=visitor)


@app.get("/events/{event_id}/summary", tags=["eventos"])
def event_summary(event_id: str, st: Stores = Depends(stores)):
    summary, hit = EventRepository(st).summary_cached(event_id)
    return {"cache_hit": hit, **summary}


@app.patch("/events/{event_id}", response_model=EventOut, tags=["eventos"])
def update_event(event_id: str, body: EventUpdate, st: Stores = Depends(stores),
                 _: dict = Depends(require_admin)):
    return EventRepository(st).update(event_id, **body.model_dump())


@app.post("/events/{event_id}/stock", tags=["eventos"])
def add_stock(event_id: str, qty: int = Query(..., description="Entradas a añadir (negativo para quitar)"),
              st: Stores = Depends(stores), _: dict = Depends(require_admin)):
    return {"event_id": event_id, "stock": EventRepository(st).add_stock(event_id, qty)}


@app.delete("/events/{event_id}", status_code=204, tags=["eventos"])
def delete_event(event_id: str, st: Stores = Depends(stores), _: dict = Depends(require_admin)):
    EventRepository(st).delete(event_id)


# --- Reservas temporales (Redis, TTL fijo) -----------------------------------
@app.post("/holds", response_model=HoldOut, status_code=201, tags=["reservas"])
def create_hold(body: HoldIn, st: Stores = Depends(stores), auth: dict = Depends(current_auth)):
    if body.user_id != auth["user_id"]:
        raise HTTPException(status_code=403, detail="La sesión no pertenece a ese usuario")
    return HoldRepository(st).create(user_id=auth["user_id"], event_id=body.event_id, qty=body.qty)


@app.get("/holds/pending", tags=["reservas"])
def pending_holds(st: Stores = Depends(stores), _: dict = Depends(require_admin)):
    repo = HoldRepository(st)
    repo.reclaim_expired()
    return repo.pending()


@app.get("/holds/{hold_id}", response_model=HoldOut, tags=["reservas"])
def get_hold(hold_id: str, st: Stores = Depends(stores), auth: dict = Depends(current_auth)):
    hold = HoldRepository(st).get(hold_id)
    _self_or_admin(auth, hold["user_id"])
    return hold


@app.delete("/holds/{hold_id}", tags=["reservas"])
def cancel_hold(hold_id: str, st: Stores = Depends(stores), auth: dict = Depends(current_auth)):
    owner = None if auth["role"] == "admin" else auth["user_id"]
    return HoldRepository(st).cancel(hold_id, user_id=owner)


@app.post("/holds/reclaim", tags=["reservas"])
def reclaim(st: Stores = Depends(stores), _: dict = Depends(require_admin)):
    return {"released": HoldRepository(st).reclaim_expired()}


@app.get("/users/{user_id}/holds", response_model=list[HoldOut], tags=["reservas"])
def user_holds(user_id: str, st: Stores = Depends(stores), auth: dict = Depends(current_auth)):
    _self_or_admin(auth, user_id)
    return HoldRepository(st).list_for_user(user_id)


# --- Órdenes (PostgreSQL, transacción ACID) ------------------------------------
@app.post("/holds/{hold_id}/confirm", response_model=OrderOut, status_code=201, tags=["ordenes"])
def confirm_hold(hold_id: str, st: Stores = Depends(stores), auth: dict = Depends(current_auth)):
    return OrderRepository(st).confirm(hold_id, user_id=auth["user_id"])


@app.get("/orders", response_model=list[OrderOut], tags=["ordenes"])
def list_orders(st: Stores = Depends(stores), _: dict = Depends(require_admin)):
    return OrderRepository(st).list_all()


@app.get("/orders/{order_id}", response_model=OrderOut, tags=["ordenes"])
def get_order(order_id: str, st: Stores = Depends(stores), auth: dict = Depends(current_auth)):
    order = OrderRepository(st).get(order_id)
    _self_or_admin(auth, order["user_id"])
    return order


@app.post("/orders/{order_id}/cancel", response_model=OrderOut, tags=["ordenes"])
def cancel_order(order_id: str, st: Stores = Depends(stores), auth: dict = Depends(current_auth)):
    owner = None if auth["role"] == "admin" else auth["user_id"]
    return OrderRepository(st).cancel(order_id, user_id=owner)


@app.get("/users/{user_id}/orders", response_model=list[OrderOut], tags=["ordenes"])
def user_orders(user_id: str, st: Stores = Depends(stores), auth: dict = Depends(current_auth)):
    _self_or_admin(auth, user_id)
    return OrderRepository(st).list_for_user(user_id)


# --- Analítica pública (Redis) ---------------------------------------------------
@app.get("/ranking", response_model=list[RankingEntry], tags=["analitica"])
def ranking(limit: int = Query(default=5, ge=1, le=50), st: Stores = Depends(stores)):
    return AnalyticsRepository(st).top_events(limit)


@app.get("/activity", tags=["analitica"])
def activity(limit: int = Query(default=10, ge=1, le=100), st: Stores = Depends(stores)):
    return AnalyticsRepository(st).recent_activity(limit)


# --- Administración sobre ambas bases (solo rol admin) -------------------------
@app.get("/admin/sales", tags=["administracion"])
def admin_sales(st: Stores = Depends(stores), _: dict = Depends(require_admin)):
    """Informe de ventas e ingresos: vista agregada de PostgreSQL."""
    return AnalyticsRepository(st).sales_report()


@app.get("/admin/audit", tags=["administracion"])
def admin_audit(limit: int = Query(default=30, ge=1, le=200), st: Stores = Depends(stores),
                _: dict = Depends(require_admin)):
    """Bitácora escrita por triggers de PostgreSQL (la app solo puede leerla)."""
    return AnalyticsRepository(st).audit_log(limit)


@app.get("/admin/consistency", tags=["administracion"])
def admin_consistency(st: Stores = Depends(stores), _: dict = Depends(require_admin)):
    """Compara PostgreSQL con Redis (stock, ventas y ranking)."""
    return AnalyticsRepository(st).consistency()


@app.post("/admin/reconcile", tags=["administracion"])
def admin_reconcile(fix: bool = Query(default=True), st: Stores = Depends(stores),
                    _: dict = Depends(require_admin)):
    """Reconstruye en Redis el estado derivado a partir de PostgreSQL."""
    return MaintenanceService(st).reconcile(fix=fix)
