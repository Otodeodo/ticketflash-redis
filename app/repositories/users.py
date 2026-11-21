"""Repositorio de usuarios (PostgreSQL) y autenticación.

* Datos permanentes en la tabla `users` (restricciones UNIQUE y CHECK).
* Contraseñas con scrypt; nunca se devuelve `password_hash`.
* Redis protege el login: cuenta intentos fallidos por correo (INCR + EXPIRE) y
  bloquea temporalmente al superar el límite (fuerza bruta).
"""

from __future__ import annotations

from psycopg import errors as pgerr

from app import keys
from app.config import settings
from app.repositories.common import (ConflictError, InvalidCredentials, NotFoundError,
                                     TooManyAttempts, to_int_id)
from app.security import MIN_PASSWORD_LENGTH, hash_password, verify_password
from app.stores import Stores

_COLS = "id, name, email, role, active, created_at"
# Hash de relleno: se verifica igualmente cuando el correo no existe, para que la
# respuesta tarde lo mismo y no se pueda averiguar qué correos están registrados.
_DUMMY_HASH = hash_password("contraseña-de-relleno")


def _public(row: dict) -> dict:
    return {"id": str(row["id"]), "name": row["name"], "email": row["email"],
            "role": row["role"], "active": row["active"],
            "created_at": row["created_at"].isoformat(timespec="seconds")}


class UserRepository:
    def __init__(self, stores: Stores):
        self.pg = stores.pg
        self.r = stores.redis.client

    # --- Create ---------------------------------------------------------------
    def create(self, *, name: str, email: str, password: str, role: str = "customer") -> dict:
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ConflictError(f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres")
        pwd_hash = hash_password(password)

        def tx(cur):
            return cur.execute(
                f"INSERT INTO users (name, email, password_hash, role) VALUES (%s, %s, %s, %s) "
                f"RETURNING {_COLS}", (name.strip(), email.strip(), pwd_hash, role)).fetchone()
        try:
            return _public(self.pg.run(tx))
        except pgerr.UniqueViolation as exc:
            raise ConflictError("Ya existe una cuenta con ese correo") from exc

    # --- Read -----------------------------------------------------------------
    def get(self, user_id: str) -> dict:
        row = self.pg.fetch_one(f"SELECT {_COLS} FROM users WHERE id = %s",
                                (to_int_id(user_id, "Usuario"),))
        if not row:
            raise NotFoundError(f"Usuario {user_id} no existe")
        return _public(row)

    def exists(self, user_id: str) -> bool:
        try:
            uid = to_int_id(user_id, "Usuario")
        except NotFoundError:
            return False
        return self.pg.fetch_one("SELECT 1 AS x FROM users WHERE id = %s AND active", (uid,)) is not None

    def list_all(self) -> list[dict]:
        return [_public(r) for r in self.pg.fetch_all(f"SELECT {_COLS} FROM users ORDER BY id")]

    # --- Autenticación ----------------------------------------------------------
    def authenticate(self, email: str, password: str) -> dict:
        attempts_key = keys.login_attempts(email)
        attempts = int(self.r.get(attempts_key) or 0)
        if attempts >= settings.login_max_attempts:
            wait = max(self.r.ttl(attempts_key), 1)
            raise TooManyAttempts(f"Demasiados intentos fallidos. Espera {wait} s.")
        row = self.pg.fetch_one(
            f"SELECT {_COLS}, password_hash FROM users WHERE lower(email) = lower(%s) AND active",
            (email.strip(),))
        ok = verify_password(password, row["password_hash"] if row else _DUMMY_HASH)
        if not (row and ok):
            pipe = self.r.pipeline(transaction=True)
            pipe.incr(attempts_key)
            pipe.expire(attempts_key, settings.login_block_seconds)
            pipe.execute()
            raise InvalidCredentials("Correo o contraseña incorrectos")
        self.r.delete(attempts_key)
        return _public(row)

    # --- Update ---------------------------------------------------------------
    def update(self, user_id: str, *, name: str | None = None, password: str | None = None) -> dict:
        sets, params = [], []
        if name is not None:
            sets.append("name = %s"); params.append(name.strip())
        if password is not None:
            if len(password) < MIN_PASSWORD_LENGTH:
                raise ConflictError(f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres")
            sets.append("password_hash = %s"); params.append(hash_password(password))
        if not sets:
            return self.get(user_id)
        params.append(to_int_id(user_id, "Usuario"))
        row = self.pg.run(lambda cur: cur.execute(
            f"UPDATE users SET {', '.join(sets)} WHERE id = %s RETURNING {_COLS}", params).fetchone())
        if not row:
            raise NotFoundError(f"Usuario {user_id} no existe")
        return _public(row)

    # --- Delete ---------------------------------------------------------------
    def delete(self, user_id: str) -> None:
        uid = to_int_id(user_id, "Usuario")
        try:
            deleted = self.pg.run(lambda cur: cur.execute(
                "DELETE FROM users WHERE id = %s", (uid,)).rowcount)
        except pgerr.ForeignKeyViolation as exc:
            raise ConflictError("El usuario tiene órdenes registradas y no puede eliminarse") from exc
        if not deleted:
            raise NotFoundError(f"Usuario {user_id} no existe")
        self.r.delete(keys.user_holds(str(uid)))
