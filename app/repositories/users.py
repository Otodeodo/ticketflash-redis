"""Repositorio de usuarios (HASH tf:user:<id> + SET tf:users)."""

from __future__ import annotations

from app import keys
from app.db import Database
from app.repositories.common import NotFoundError, next_id, now_iso


class UserRepository:
    def __init__(self, db: Database):
        self.db = db
        self.r = db.client

    def create(self, *, name: str, email: str) -> dict:
        user_id = next_id(self.db, "user")
        data = {"id": user_id, "name": name, "email": email, "created_at": now_iso()}
        pipe = self.r.pipeline(transaction=True)
        pipe.hset(keys.user(user_id), mapping=data)
        pipe.sadd(keys.users_index(), user_id)
        pipe.execute()
        return data

    def get(self, user_id: str) -> dict:
        data = self.r.hgetall(keys.user(user_id))
        if not data:
            raise NotFoundError(f"Usuario {user_id} no existe")
        return data

    def list_all(self) -> list[dict]:
        ids = sorted(self.r.smembers(keys.users_index()), key=int)
        pipe = self.r.pipeline(transaction=False)
        for user_id in ids:
            pipe.hgetall(keys.user(user_id))
        return [u for u in pipe.execute() if u]

    def update(self, user_id: str, **fields) -> dict:
        key = keys.user(user_id)
        if not self.r.exists(key):
            raise NotFoundError(f"Usuario {user_id} no existe")
        clean = {k: v for k, v in fields.items() if v is not None}
        if clean:
            self.r.hset(key, mapping=clean)
        return self.get(user_id)

    def delete(self, user_id: str) -> None:
        if not self.r.exists(keys.user(user_id)):
            raise NotFoundError(f"Usuario {user_id} no existe")
        pipe = self.r.pipeline(transaction=True)
        pipe.delete(keys.user(user_id), keys.user_orders(user_id), keys.user_holds(user_id))
        pipe.srem(keys.users_index(), user_id)
        pipe.execute()
