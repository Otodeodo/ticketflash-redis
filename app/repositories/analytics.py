"""Consultas analíticas: ranking (ZSET), feed de actividad (LIST) y estado."""

from __future__ import annotations

import json

from app import keys
from app.db import Database


class AnalyticsRepository:
    def __init__(self, db: Database):
        self.db = db
        self.r = db.client

    def top_events(self, limit: int = 5) -> list[dict]:
        """ZREVRANGE: los eventos con más entradas vendidas, O(log N + M)."""
        rows = self.r.zrevrange(keys.ranking_sales(), 0, limit - 1, withscores=True)
        if not rows:
            return []
        pipe = self.r.pipeline(transaction=False)
        for event_id, _ in rows:
            pipe.hget(keys.event(event_id), "name")
        names = pipe.execute()
        return [
            {"event_id": event_id, "name": name or "(eliminado)", "sold": int(score)}
            for (event_id, score), name in zip(rows, names)
        ]

    def rank_of(self, event_id: str) -> int | None:
        """Posición (0 = primero) de un evento en el ranking."""
        rank = self.r.zrevrank(keys.ranking_sales(), event_id)
        return None if rank is None else int(rank)

    def recent_activity(self, limit: int = 10) -> list[dict]:
        return [json.loads(x) for x in self.r.lrange(keys.activity(), 0, limit - 1)]

    def key_overview(self) -> dict:
        """Cuenta claves por tipo de estructura (para la defensa técnica)."""
        counts: dict[str, int] = {}
        for key in self.r.scan_iter(match=keys.all_pattern(), count=200):
            kind = self.r.type(key)
            counts[kind] = counts.get(kind, 0) + 1
        return counts
