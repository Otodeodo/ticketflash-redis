"""Interfaz de línea de comandos para la demostración en vivo.

Ejemplos:
    python -m app.cli status
    python -m app.cli events
    python -m app.cli login 1
    python -m app.cli hold --user 1 --event 2 --qty 2
    python -m app.cli confirm 1
    python -m app.cli ranking
    python -m app.cli demo
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from app.db import get_db
from app.repositories import (AnalyticsRepository, EventRepository, HoldRepository,
                              OrderRepository, SessionRepository, UserRepository)
from app.repositories.common import ConflictError, NotFoundError


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_status(_args):
    db = get_db()
    _print({"ping": db.ping(), **db.info_summary(), "keys_by_type": AnalyticsRepository(db).key_overview()})


def cmd_events(args):
    repo = EventRepository(get_db())
    events = repo.list_by_category(args.category) if args.category else repo.list_all()
    print(f"{'ID':<4}{'Evento':<34}{'Categoría':<12}{'Precio':>8}{'Stock':>7}{'Vendidas':>10}")
    for e in events:
        print(f"{e['id']:<4}{e['name'][:33]:<34}{e['category']:<12}{e['price']:>8.2f}{e['stock']:>7}{e['sold']:>10}")


def cmd_event(args):
    _print(EventRepository(get_db()).get(args.event_id))


def cmd_users(_args):
    for u in UserRepository(get_db()).list_all():
        print(f"{u['id']:<4}{u['name']:<24}{u['email']}")


def cmd_login(args):
    _print(SessionRepository(get_db()).login(args.user_id))


def cmd_session(args):
    _print(SessionRepository(get_db()).touch(args.token))


def cmd_hold(args):
    _print(HoldRepository(get_db()).create(user_id=args.user, event_id=args.event, qty=args.qty))


def cmd_holds(_args):
    repo = HoldRepository(get_db())
    repo.reclaim_expired()
    _print(repo.pending())


def cmd_cancel_hold(args):
    _print(HoldRepository(get_db()).cancel(args.hold_id))


def cmd_confirm(args):
    _print(OrderRepository(get_db()).confirm(args.hold_id))


def cmd_orders(args):
    repo = OrderRepository(get_db())
    _print(repo.list_for_user(args.user) if args.user else repo.list_all())


def cmd_ranking(args):
    repo = AnalyticsRepository(get_db())
    for i, row in enumerate(repo.top_events(args.limit), start=1):
        print(f"{i}. {row['name']} ({row['sold']} entradas)")


def cmd_activity(args):
    _print(AnalyticsRepository(get_db()).recent_activity(args.limit))


def cmd_reclaim(_args):
    print(f"Reservas expiradas liberadas: {HoldRepository(get_db()).reclaim_expired()}")


def cmd_demo(_args):
    """Flujo completo de extremo a extremo para la exposición."""
    db = get_db()
    users, events = UserRepository(db), EventRepository(db)
    sessions, holds, orders, analytics = (SessionRepository(db), HoldRepository(db),
                                          OrderRepository(db), AnalyticsRepository(db))

    print("1) Conexión a Redis Cloud:", db.info_summary())
    user = users.list_all()[0]
    event = events.list_all()[0]
    print(f"2) Usuario {user['name']} inicia sesión (TTL deslizante)")
    session = sessions.login(user["id"])
    print("   token:", session["token"][:12] + "...", "ttl:", session["ttl_seconds"], "s")

    print(f"3) Evento '{event['name']}' stock inicial: {event['stock']}")
    hold = holds.create(user_id=user["id"], event_id=event["id"], qty=2)
    print(f"   reserva {hold['id']} creada, expira en {hold['ttl_seconds']} s")
    print("   stock tras la reserva:", events.get(event["id"])["stock"])

    print("4) Intento de reserva por encima del stock (debe fallar):")
    try:
        holds.create(user_id=user["id"], event_id=event["id"], qty=99999)
    except ConflictError as exc:
        print("   ->", exc)

    print("5) Confirmar la reserva -> orden")
    order = orders.confirm(hold["id"])
    print(f"   orden {order['id']} total {order['total']:.2f}")
    print("   stock final:", events.get(event["id"])["stock"])

    print("6) Ranking de ventas (ZSET):")
    for i, row in enumerate(analytics.top_events(3), start=1):
        print(f"   {i}. {row['name']} ({row['sold']})")

    print("7) Caché de resumen (TTL corto):")
    _, hit1 = events.summary_cached(event["id"])
    _, hit2 = events.summary_cached(event["id"])
    print(f"   primera lectura hit={hit1}, segunda lectura hit={hit2}")

    print("8) Sesión sigue viva, TTL restante:", sessions.ttl(session["token"]), "s")
    print("Demo completada.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ticketflash", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Estado de la conexión y conteo de claves").set_defaults(fn=cmd_status)
    p = sub.add_parser("events", help="Listar eventos"); p.add_argument("--category"); p.set_defaults(fn=cmd_events)
    p = sub.add_parser("event", help="Detalle de un evento"); p.add_argument("event_id"); p.set_defaults(fn=cmd_event)
    sub.add_parser("users", help="Listar usuarios").set_defaults(fn=cmd_users)
    p = sub.add_parser("login", help="Crear sesión"); p.add_argument("user_id"); p.set_defaults(fn=cmd_login)
    p = sub.add_parser("session", help="Validar/renovar sesión"); p.add_argument("token"); p.set_defaults(fn=cmd_session)
    p = sub.add_parser("hold", help="Crear reserva temporal")
    p.add_argument("--user", required=True); p.add_argument("--event", required=True)
    p.add_argument("--qty", type=int, default=1); p.set_defaults(fn=cmd_hold)
    sub.add_parser("holds", help="Reservas pendientes").set_defaults(fn=cmd_holds)
    p = sub.add_parser("cancel-hold", help="Cancelar reserva"); p.add_argument("hold_id"); p.set_defaults(fn=cmd_cancel_hold)
    p = sub.add_parser("confirm", help="Confirmar reserva -> orden"); p.add_argument("hold_id"); p.set_defaults(fn=cmd_confirm)
    p = sub.add_parser("orders", help="Listar órdenes"); p.add_argument("--user"); p.set_defaults(fn=cmd_orders)
    p = sub.add_parser("ranking", help="Top eventos por ventas"); p.add_argument("--limit", type=int, default=5); p.set_defaults(fn=cmd_ranking)
    p = sub.add_parser("activity", help="Actividad reciente"); p.add_argument("--limit", type=int, default=10); p.set_defaults(fn=cmd_activity)
    sub.add_parser("reclaim", help="Liberar reservas expiradas").set_defaults(fn=cmd_reclaim)
    sub.add_parser("demo", help="Flujo completo de demostración").set_defaults(fn=cmd_demo)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except (NotFoundError, ConflictError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
