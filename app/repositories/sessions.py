"""Repositorio de sesiones con TTL deslizante (Redis).

HASH tf:session:<token> con EXPIRE. Cada acceso válido renueva el TTL
(sliding expiration), igual que un almacén de sesiones de un sitio web.
Redis elimina la clave automáticamente cuando el usuario queda inactivo.
La sesión guarda user_id y role: así la API autoriza sin consultar PostgreSQL
en cada petición.
"""

from __future__ import annotations

import secrets

from app import keys
from app.config import settings
from app.repositories.common import NotFoundError, now_iso
from app.stores import Stores


class SessionRepository:
    def __init__(self, stores: Stores):
        self.r = stores.redis.client

    def login(self, user: dict) -> dict:
        """Crea la sesión para un usuario ya autenticado (dict público de UserRepository)."""
        token = secrets.token_urlsafe(24)
        key = keys.session(token)
        pipe = self.r.pipeline(transaction=True)
        pipe.hset(key, mapping={"user_id": user["id"], "role": user["role"],
                                "created_at": now_iso(), "last_seen": now_iso()})
        pipe.expire(key, settings.session_ttl)
        pipe.execute()
        return {"token": token, "user_id": user["id"], "role": user["role"],
                "ttl_seconds": settings.session_ttl}

    def touch(self, token: str) -> dict:
        """Valida la sesión y renueva su TTL. Lanza NotFoundError si expiró."""
        key = keys.session(token)
        pipe = self.r.pipeline(transaction=True)
        pipe.hgetall(key)
        pipe.ttl(key)
        data, ttl = pipe.execute()
        if not data:
            raise NotFoundError("Sesión inválida o expirada")
        pipe = self.r.pipeline(transaction=True)
        pipe.hset(key, "last_seen", now_iso())
        pipe.expire(key, settings.session_ttl)
        pipe.execute()
        return {"token": token, "user_id": data["user_id"], "role": data.get("role", "customer"),
                "ttl_seconds": settings.session_ttl, "previous_ttl": ttl}

    def ttl(self, token: str) -> int:
        return self.r.ttl(keys.session(token))

    def logout(self, token: str) -> bool:
        return self.r.delete(keys.session(token)) == 1
