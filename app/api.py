"""API REST (FastAPI) de TicketFlash.

Ejecutar:  uvicorn app.api:app --reload
App web:   http://127.0.0.1:8000/       (interfaz de usuario)
Docs:      http://127.0.0.1:8000/docs   (Swagger UI)
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.db import Database, get_db
from app.models import (EventIn, EventOut, EventUpdate, HoldIn, HoldOut, LoginIn, OrderOut,
                        RankingEntry, SessionOut, UserIn, UserOut)
from app.repositories import (AnalyticsRepository, EventRepository, HoldRepository,
                              OrderRepository, SessionRepository, UserRepository)
from app.repositories.common import ConflictError, NotFoundError

app = FastAPI(
    title="TicketFlash API",
    version=__version__,
    description="Venta de entradas con reservas temporales (TTL) sobre Redis Cloud.",
)


# --- Interfaz web (aplicación de usuario) -------------------------------------
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    """Sirve la aplicación web (app/static/index.html)."""
    return FileResponse(STATIC_DIR / "index.html")


# --- Manejo de errores -------------------------------------------------------
@app.exception_handler(NotFoundError)
async def _not_found(_: Request, exc: NotFoundError):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(ConflictError)
async def _conflict(_: Request, exc: ConflictError):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


# --- Dependencias ------------------------------------------------------------
def db() -> Database:
    return get_db()


def current_user(db: Database = Depends(db),
                 x_session_token: str = Header(..., alias="X-Session-Token")) -> str:
    """Valida el token de sesión y renueva su TTL. Devuelve el user_id."""
    try:
        return SessionRepository(db).touch(x_session_token)["user_id"]
    except NotFoundError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


# --- Estado ------------------------------------------------------------------
@app.get("/health", tags=["estado"])
def health(db: Database = Depends(db)):
    return {"ok": db.ping(), **db.info_summary()}


@app.get("/stats/keys", tags=["estado"])
def key_overview(db: Database = Depends(db)):
    return AnalyticsRepository(db).key_overview()


# --- Usuarios ----------------------------------------------------------------
@app.post("/users", response_model=UserOut, status_code=201, tags=["usuarios"])
def create_user(body: UserIn, db: Database = Depends(db)):
    return UserRepository(db).create(name=body.name, email=body.email)


@app.get("/users", response_model=list[UserOut], tags=["usuarios"])
def list_users(db: Database = Depends(db)):
    return UserRepository(db).list_all()


@app.get("/users/{user_id}", response_model=UserOut, tags=["usuarios"])
def get_user(user_id: str, db: Database = Depends(db)):
    return UserRepository(db).get(user_id)


@app.delete("/users/{user_id}", status_code=204, tags=["usuarios"])
def delete_user(user_id: str, db: Database = Depends(db)):
    UserRepository(db).delete(user_id)


# --- Sesiones (TTL deslizante) ------------------------------------------------
@app.post("/auth/login", response_model=SessionOut, tags=["sesiones"])
def login(body: LoginIn, db: Database = Depends(db)):
    return SessionRepository(db).login(body.user_id)


@app.get("/auth/me", tags=["sesiones"])
def me(db: Database = Depends(db), user_id: str = Depends(current_user),
       x_session_token: str = Header(..., alias="X-Session-Token")):
    return {"user": UserRepository(db).get(user_id),
            "session_ttl": SessionRepository(db).ttl(x_session_token)}


@app.post("/auth/logout", tags=["sesiones"])
def logout(db: Database = Depends(db),
           x_session_token: str = Header(..., alias="X-Session-Token")):
    return {"logged_out": SessionRepository(db).logout(x_session_token)}


# --- Eventos (CRUD) ----------------------------------------------------------
@app.post("/events", response_model=EventOut, status_code=201, tags=["eventos"])
def create_event(body: EventIn, db: Database = Depends(db)):
    return EventRepository(db).create(**body.model_dump())


@app.get("/events", response_model=list[EventOut], tags=["eventos"])
def list_events(category: str | None = Query(default=None), db: Database = Depends(db)):
    repo = EventRepository(db)
    return repo.list_by_category(category) if category else repo.list_all()


@app.get("/events/categories", tags=["eventos"])
def list_categories(db: Database = Depends(db)):
    return EventRepository(db).categories()


@app.get("/events/{event_id}", response_model=EventOut, tags=["eventos"])
def get_event(event_id: str, request: Request, db: Database = Depends(db)):
    visitor = request.headers.get("X-Session-Token") or (request.client.host if request.client else None)
    return EventRepository(db).get(event_id, track_visitor=visitor)


@app.get("/events/{event_id}/summary", tags=["eventos"])
def event_summary(event_id: str, db: Database = Depends(db)):
    summary, hit = EventRepository(db).summary_cached(event_id)
    return {"cache_hit": hit, **summary}


@app.patch("/events/{event_id}", response_model=EventOut, tags=["eventos"])
def update_event(event_id: str, body: EventUpdate, db: Database = Depends(db)):
    return EventRepository(db).update(event_id, **body.model_dump())


@app.post("/events/{event_id}/stock", tags=["eventos"])
def add_stock(event_id: str, qty: int = Query(..., description="Entradas a añadir (negativo para quitar)"), db: Database = Depends(db)):
    return {"event_id": event_id, "stock": EventRepository(db).add_stock(event_id, qty)}


@app.delete("/events/{event_id}", status_code=204, tags=["eventos"])
def delete_event(event_id: str, db: Database = Depends(db)):
    EventRepository(db).delete(event_id)


# --- Reservas temporales (TTL fijo) -----------------------------------------
@app.post("/holds", response_model=HoldOut, status_code=201, tags=["reservas"])
def create_hold(body: HoldIn, db: Database = Depends(db), user_id: str = Depends(current_user)):
    if body.user_id != user_id:
        raise HTTPException(status_code=403, detail="La sesión no pertenece a ese usuario")
    return HoldRepository(db).create(user_id=user_id, event_id=body.event_id, qty=body.qty)


@app.get("/holds/pending", tags=["reservas"])
def pending_holds(db: Database = Depends(db)):
    repo = HoldRepository(db)
    repo.reclaim_expired()
    return repo.pending()


@app.get("/holds/{hold_id}", response_model=HoldOut, tags=["reservas"])
def get_hold(hold_id: str, db: Database = Depends(db)):
    return HoldRepository(db).get(hold_id)


@app.delete("/holds/{hold_id}", tags=["reservas"])
def cancel_hold(hold_id: str, db: Database = Depends(db), user_id: str = Depends(current_user)):
    return HoldRepository(db).cancel(hold_id)


@app.post("/holds/reclaim", tags=["reservas"])
def reclaim(db: Database = Depends(db)):
    return {"released": HoldRepository(db).reclaim_expired()}


@app.get("/users/{user_id}/holds", response_model=list[HoldOut], tags=["reservas"])
def user_holds(user_id: str, db: Database = Depends(db)):
    return HoldRepository(db).list_for_user(user_id)


# --- Órdenes -----------------------------------------------------------------
@app.post("/holds/{hold_id}/confirm", response_model=OrderOut, status_code=201, tags=["ordenes"])
def confirm_hold(hold_id: str, db: Database = Depends(db), user_id: str = Depends(current_user)):
    return OrderRepository(db).confirm(hold_id)


@app.get("/orders", response_model=list[OrderOut], tags=["ordenes"])
def list_orders(db: Database = Depends(db)):
    return OrderRepository(db).list_all()


@app.get("/orders/{order_id}", response_model=OrderOut, tags=["ordenes"])
def get_order(order_id: str, db: Database = Depends(db)):
    return OrderRepository(db).get(order_id)


@app.post("/orders/{order_id}/cancel", response_model=OrderOut, tags=["ordenes"])
def cancel_order(order_id: str, db: Database = Depends(db), user_id: str = Depends(current_user)):
    return OrderRepository(db).cancel(order_id)


@app.delete("/orders/{order_id}", status_code=204, tags=["ordenes"])
def delete_order(order_id: str, db: Database = Depends(db)):
    OrderRepository(db).delete(order_id)


@app.get("/users/{user_id}/orders", response_model=list[OrderOut], tags=["ordenes"])
def user_orders(user_id: str, db: Database = Depends(db)):
    return OrderRepository(db).list_for_user(user_id)


# --- Analítica ---------------------------------------------------------------
@app.get("/ranking", response_model=list[RankingEntry], tags=["analitica"])
def ranking(limit: int = Query(default=5, ge=1, le=50), db: Database = Depends(db)):
    return AnalyticsRepository(db).top_events(limit)


@app.get("/activity", tags=["analitica"])
def activity(limit: int = Query(default=10, ge=1, le=100), db: Database = Depends(db)):
    return AnalyticsRepository(db).recent_activity(limit)
