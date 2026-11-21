"""Interfaz de línea de comandos para la demostración en vivo (ambas bases).

Ejemplos:
    python -m app.cli status
    python -m app.cli events
    python -m app.cli login ana@example.com
    python -m app.cli hold --user 2 --event 2 --qty 2
    python -m app.cli confirm <hold_id> --user 2
    python -m app.cli consistency
    python -m app.cli demo
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys

from app.repositories import (AnalyticsRepository, EventRepository, HoldRepository,
                              MaintenanceService, OrderRepository, SessionRepository,
                              UserRepository)
from app.repositories.common import (ConflictError, ForbiddenError, InvalidCredentials,
                                     NotFoundError, TooManyAttempts)
from app.stores import get_stores


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def cmd_status(_args):
    st = get_stores()
    _print({"health": st.health(), "postgres_rows": st.pg.table_counts(),
            "redis_keys_by_type": AnalyticsRepository(st).key_overview()})


def cmd_events(args):
    repo = EventRepository(get_stores())
    events = repo.list_by_category(args.category) if args.category else repo.list_all()
    print(f"{'ID':<4}{'Evento':<34}{'Categoría':<12}{'Precio':>8}{'Stock':>7}{'Vendidas':>10}")
    for e in events:
        print(f"{e['id']:<4}{e['name'][:33]:<34}{e['category']:<12}{e['price']:>8.2f}{e['stock']:>7}{e['sold']:>10}")


def cmd_event(args):
    _print(EventRepository(get_stores()).get(args.event_id))


def cmd_users(_args):
    for u in UserRepository(get_stores()).list_all():
        print(f"{u['id']:<4}{u['name']:<24}{u['email']:<32}{u['role']}")


def cmd_register(args):
    password = args.password or os.getenv("TF_PASSWORD") or getpass.getpass("Contraseña: ")
    _print(UserRepository(get_stores()).create(name=args.name, email=args.email, password=password))


def cmd_login(args):
    st = get_stores()
    password = args.password or os.getenv("TF_PASSWORD") or getpass.getpass("Contraseña: ")
    user = UserRepository(st).authenticate(args.email, password)
    _print(SessionRepository(st).login(user))


def cmd_session(args):
    _print(SessionRepository(get_stores()).touch(args.token))


def cmd_hold(args):
    _print(HoldRepository(get_stores()).create(user_id=args.user, event_id=args.event, qty=args.qty))


def cmd_holds(_args):
    repo = HoldRepository(get_stores())
    repo.reclaim_expired()
    _print(repo.pending())


def cmd_cancel_hold(args):
    _print(HoldRepository(get_stores()).cancel(args.hold_id))


def cmd_confirm(args):
    _print(OrderRepository(get_stores()).confirm(args.hold_id, user_id=args.user))


def cmd_orders(args):
    repo = OrderRepository(get_stores())
    _print(repo.list_for_user(args.user) if args.user else repo.list_all())


def cmd_cancel_order(args):
    _print(OrderRepository(get_stores()).cancel(args.order_id))


def cmd_ranking(args):
    for i, row in enumerate(AnalyticsRepository(get_stores()).top_events(args.limit), start=1):
        print(f"{i}. {row['name']} ({row['sold']} entradas)")


def cmd_sales(_args):
    print(f"{'Evento':<36}{'Entradas':>9}{'Ingresos':>12}")
    for r in AnalyticsRepository(get_stores()).sales_report():
        print(f"{r['name'][:35]:<36}{r['tickets_sold']:>9}{r['revenue']:>12.2f}")


def cmd_audit(args):
    for a in AnalyticsRepository(get_stores()).audit_log(args.limit):
        print(f"{a['at']}  {a['db_user']:<10}{a['action']:<8}{a['entity']:<7}#{a['entity_id']}  {a['detail']}")


def cmd_activity(args):
    _print(AnalyticsRepository(get_stores()).recent_activity(args.limit))


def cmd_reclaim(_args):
    print(f"Reservas expiradas liberadas: {HoldRepository(get_stores()).reclaim_expired()}")


def cmd_consistency(_args):
    report = AnalyticsRepository(get_stores()).consistency()
    print(f"{'Evento':<30}{'PG vendidas':>12}{'Reservadas':>11}{'Redis stock':>12}{'Esperado':>9}  Estado")
    for e in report["events"]:
        print(f"{e['name'][:29]:<30}{e['pg_sold']:>12}{e['held']:>11}{str(e['redis_stock']):>12}"
              f"{e['redis_expected']:>9}  {'OK' if e['ok'] else 'DESFASE'}")
    print("\nConsistencia global:", "OK" if report["ok"] else "HAY DESFASES (ejecuta: reconcile)")


def cmd_reconcile(args):
    _print(MaintenanceService(get_stores()).reconcile(fix=not args.dry_run))


def cmd_demo(_args):
    """Flujo completo de extremo a extremo para la exposición."""
    st = get_stores()
    users, events = UserRepository(st), EventRepository(st)
    sessions, holds, orders, analytics = (SessionRepository(st), HoldRepository(st),
                                          OrderRepository(st), AnalyticsRepository(st))
    health = st.health()
    print(f"1) Conexión remota: PostgreSQL {health.get('pg_version')} como '{health.get('pg_user')}' "
          f"y Redis {health.get('redis_version')}")
    customers = [u for u in users.list_all() if u["role"] == "customer"]
    if not customers:
        print("   No hay usuarios: ejecuta  python scripts/seed.py --reset"); return
    user, event = customers[0], events.list_all()[0]
    print(f"2) {user['name']} inicia sesión: la sesión vive en Redis con TTL deslizante")
    session = sessions.login(user)
    print("   token:", session["token"][:12] + "...", "ttl:", session["ttl_seconds"], "s")

    print(f"3) Evento '{event['name']}'  stock Redis: {event['stock']}  vendidas (PostgreSQL): {event['sold']}")
    hold = holds.create(user_id=user["id"], event_id=event["id"], qty=2)
    print(f"   reserva {hold['id']} (Redis, TTL {hold['ttl_seconds']} s)  stock ahora:", events.get(event["id"])["stock"])

    print("4) Reserva por encima del stock (debe fallar sin tocar nada):")
    try:
        holds.create(user_id=user["id"], event_id=event["id"], qty=99999)
    except ConflictError as exc:
        print("   ->", exc)

    print("5) Confirmar la reserva = saga Redis + transacción ACID en PostgreSQL")
    order = orders.confirm(hold["id"], user_id=user["id"])
    now = events.get(event["id"])
    print(f"   orden {order['id']} total {order['total']:.2f}   vendidas (PostgreSQL): {now['sold']}   stock Redis: {now['stock']}")

    print("6) Ranking de ventas (ZSET de Redis):")
    for i, row in enumerate(analytics.top_events(3), start=1):
        print(f"   {i}. {row['name']} ({row['sold']})")
    print("7) Informe de ingresos (vista SQL en PostgreSQL):")
    for r in analytics.sales_report(3):
        print(f"   {r['name']}: {r['tickets_sold']} entradas, {r['revenue']:.2f}")
    print("8) Auditoría escrita por triggers:", analytics.audit_log(1)[0]["action"], "order")
    print("9) Consistencia PostgreSQL <-> Redis:", "OK" if analytics.consistency()["ok"] else "DESFASE")
    print("Demo completada.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ticketflash", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, help_, fn, *args):
        p = sub.add_parser(name, help=help_)
        for a, kw in args:
            p.add_argument(a, **kw)
        p.set_defaults(fn=fn)
        return p

    add("status", "Estado de ambas conexiones y conteos", cmd_status)
    add("events", "Listar eventos", cmd_events, ("--category", {}))
    add("event", "Detalle de un evento", cmd_event, ("event_id", {}))
    add("users", "Listar usuarios (PostgreSQL)", cmd_users)
    add("register", "Crear usuario", cmd_register, ("name", {}), ("email", {}), ("--password", {}))
    add("login", "Autenticar y crear sesión en Redis", cmd_login, ("email", {}), ("--password", {}))
    add("session", "Validar/renovar sesión", cmd_session, ("token", {}))
    add("hold", "Crear reserva temporal", cmd_hold, ("--user", {"required": True}),
        ("--event", {"required": True}), ("--qty", {"type": int, "default": 1}))
    add("holds", "Reservas pendientes", cmd_holds)
    add("cancel-hold", "Cancelar reserva", cmd_cancel_hold, ("hold_id", {}))
    add("confirm", "Confirmar reserva -> orden", cmd_confirm, ("hold_id", {}), ("--user", {}))
    add("orders", "Listar órdenes", cmd_orders, ("--user", {}))
    add("cancel-order", "Cancelar una orden", cmd_cancel_order, ("order_id", {}))
    add("ranking", "Top eventos (Redis)", cmd_ranking, ("--limit", {"type": int, "default": 5}))
    add("sales", "Informe de ventas (PostgreSQL)", cmd_sales)
    add("audit", "Bitácora de auditoría (PostgreSQL)", cmd_audit, ("--limit", {"type": int, "default": 15}))
    add("activity", "Actividad reciente (Redis)", cmd_activity, ("--limit", {"type": int, "default": 10}))
    add("reclaim", "Liberar reservas expiradas", cmd_reclaim)
    add("consistency", "Comparar PostgreSQL y Redis", cmd_consistency)
    add("reconcile", "Reconstruir Redis desde PostgreSQL", cmd_reconcile,
        ("--dry-run", {"action": "store_true"}))
    add("demo", "Flujo completo de demostración", cmd_demo)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except (NotFoundError, ConflictError, ForbiddenError, InvalidCredentials, TooManyAttempts) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
