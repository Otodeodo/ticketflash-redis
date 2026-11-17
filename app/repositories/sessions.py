"""Repositorio de sesiones con TTL deslizante.

HASH tf:session:<token> con EXPIRE. Cada acceso válido renueva el TTL
(sliding expiration), igual que un almacén de sesiones de un sitio web.
Redis elimina la clave automáticamente cuando el usuario queda inactivo.
"""

from __future__ import annotations

import secrets

from app import keys
from app.config import settings
from app.db import Database
from app.repositories.common import NotFoundError, now_iso


class SessionRepository:
    def __init__(self, db: Database):
        self.db = db
        self.r = db.client

    def login(self, user_id: str) -> dict:
        if not self.r.exists(keys.user(user_id)):
            raise NotFoundError(f"Usuario {user_id} no existe")
        token = secrets.token_urlsafe(24)
        key = keys.session(token)
        pipe = self.r.pipeline(transaction=True)
        pipe.hset(key, mapping={"user_id": user_id, "created_at": now_iso(),
                                "last_seen": now_iso()})
        pipe.expire(key, settings.session_ttl)
        pipe.execute()
        return {"token": token, "user_id": user_id, "ttl_seconds": settings.session_ttl}

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
        return {"token": token, "user_id": data["user_id"], "ttl_seconds": settings.session_ttl,
                "previous_ttl": ttl}

    def ttl(self, token: str) -> int:
        return self.r.ttl(keys.session(token))

    def logout(self, token: str) -> bool:
        return self.r.delete(keys.session(token)) == 1
