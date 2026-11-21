"""Consultas analíticas y administración sobre ambas bases.

Redis  : ranking (ZSET), feed de actividad (LIST) y conteo de claves.
Postgres: informe de ventas (vista con agregación), auditoría y consistencia.
"""

from __future__ import annotations

import json

from app import keys
from app.repositories.common import now_ts
from app.stores import Stores


class AnalyticsRepository:
    def __init__(self, stores: Stores):
        self.pg = stores.pg
        self.r = stores.redis.client

    # --- Redis ------------------------------------------------------------------
    def top_events(self, limit: int = 5) -> list[dict]:
        """ZREVRANGE: los eventos con más entradas vendidas, O(log N + M)."""
        rows = [(eid, score) for eid, score in
                self.r.zrevrange(keys.ranking_sales(), 0, limit - 1, withscores=True) if score > 0]
        if not rows:
            return []
        names = {str(r["id"]): r["name"] for r in self.pg.fetch_all(
            "SELECT id, name FROM events WHERE id = ANY(%s)", ([int(e) for e, _ in rows],))}
        return [{"event_id": eid, "name": names.get(eid, "(eliminado)"), "sold": int(score)}
                for eid, score in rows]

    def recent_activity(self, limit: int = 10) -> list[dict]:
        return [json.loads(x) for x in self.r.lrange(keys.activity(), 0, limit - 1)]

    def key_overview(self) -> dict:
        """Cuenta claves por tipo de estructura (para la defensa técnica)."""
        counts: dict[str, int] = {}
        for key in self.r.scan_iter(match=keys.all_pattern(), count=200):
            kind = self.r.type(key)
            counts[kind] = counts.get(kind, 0) + 1
        return counts

    # --- PostgreSQL ---------------------------------------------------------------
    def sales_report(self, limit: int = 20) -> list[dict]:
        """Ventas e ingresos por evento (vista con agregación FILTER)."""
        rows = self.pg.fetch_all(
            "SELECT event_id, name, tickets_sold, revenue, orders_confirmed "
            "FROM v_sales_by_event ORDER BY revenue DESC, event_id LIMIT %s", (limit,))
        return [{"event_id": str(r["event_id"]), "name": r["name"], "tickets_sold": int(r["tickets_sold"]),
                 "revenue": float(r["revenue"]), "orders": int(r["orders_confirmed"])} for r in rows]

    def audit_log(self, limit: int = 30) -> list[dict]:
        rows = self.pg.fetch_all(
            "SELECT id, at, db_user, action, entity, entity_id, detail FROM audit_log "
            "ORDER BY at DESC, id DESC LIMIT %s", (limit,))
        return [{"id": r["id"], "at": r["at"].isoformat(timespec="seconds"), "db_user": r["db_user"],
                 "action": r["action"], "entity": r["entity"], "entity_id": r["entity_id"],
                 "detail": r["detail"]} for r in rows]

    # --- Consistencia entre bases -----------------------------------------------------
    def consistency(self) -> dict:
        """Compara PostgreSQL contra Redis. Útil para demostrar que ambas coinciden.

            esperado_en_redis = capacity - sold - entradas en reservas activas
        """
        pg_rows = self.pg.fetch_all(
            "SELECT e.id, e.name, e.capacity, e.sold, s.orders_qty, s.consistent "
            "FROM events e JOIN v_sold_consistency s ON s.event_id = e.id ORDER BY e.id")
        now = now_ts()
        held: dict[str, int] = {}
        for member, score in self.r.zrange(keys.holds_expiring(), 0, -1, withscores=True):
            _, event_id, _, qty = keys.parse_hold_member(member)
            if int(score) > now:
                held[event_id] = held.get(event_id, 0) + qty
        pipe = self.r.pipeline(transaction=False)
        for r in pg_rows:
            pipe.get(keys.event_stock(str(r["id"])))
            pipe.zscore(keys.ranking_sales(), str(r["id"]))
        res = pipe.execute()
        items, ok = [], True
        for i, r in enumerate(pg_rows):
            eid = str(r["id"])
            redis_stock = None if res[2 * i] is None else int(res[2 * i])
            ranking = int(float(res[2 * i + 1] or 0))
            expected = r["capacity"] - r["sold"] - held.get(eid, 0)
            row_ok = bool(r["consistent"]) and redis_stock == expected and ranking == r["sold"]
            ok = ok and row_ok
            items.append({"event_id": eid, "name": r["name"], "capacity": r["capacity"],
                          "pg_sold": r["sold"], "pg_orders_qty": r["orders_qty"],
                          "held": held.get(eid, 0), "redis_stock": redis_stock,
                          "redis_expected": expected, "redis_ranking": ranking, "ok": row_ok})
        return {"ok": ok, "events": items}
