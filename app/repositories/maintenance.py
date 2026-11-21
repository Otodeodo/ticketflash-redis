"""Reconciliación PostgreSQL -> Redis.

PostgreSQL es la fuente de verdad. Si Redis pierde datos (reinicio sin
persistencia, `FLUSHDB`, eviction) o queda desfasado por un fallo entre los pasos
de la saga de compra, este módulo reconstruye el estado derivado:

    stock   = capacity - sold - entradas en reservas activas
    ranking = events.sold por evento
"""

from __future__ import annotations

from app import keys
from app.repositories.analytics import AnalyticsRepository
from app.repositories.holds import HoldRepository
from app.stores import Stores


class MaintenanceService:
    def __init__(self, stores: Stores):
        self.stores = stores
        self.r = stores.redis.client

    def reconcile(self, *, fix: bool = True) -> dict:
        """Detecta (y si `fix`, corrige) las diferencias entre ambas bases.

        Nota: ejecutarlo mientras hay reservas en vuelo puede mostrar desfases
        transitorios de milisegundos; el uso normal es tras una caída o restauración.
        """
        HoldRepository(self.stores).reclaim_expired()
        before = AnalyticsRepository(self.stores).consistency()
        fixed = 0
        if fix:
            pipe = self.r.pipeline(transaction=True)
            for item in before["events"]:
                if item["redis_stock"] != item["redis_expected"]:
                    pipe.set(keys.event_stock(item["event_id"]), item["redis_expected"])
                    fixed += 1
                if item["redis_ranking"] != item["pg_sold"]:
                    pipe.zadd(keys.ranking_sales(), {item["event_id"]: item["pg_sold"]})
                    fixed += 1
                pipe.delete(keys.event_summary_cache(item["event_id"]))
            pipe.execute()
        return {"drift_found": sum(1 for i in before["events"] if not i["ok"]),
                "fixed": fixed, "was_consistent": before["ok"],
                "now_consistent": AnalyticsRepository(self.stores).consistency()["ok"] if fix else None}
