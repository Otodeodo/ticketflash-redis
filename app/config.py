"""Configuración de la aplicación leída desde variables de entorno.

Las credenciales NUNCA se escriben en el código: se cargan desde el archivo
`.env` (ignorado por git) o desde el entorno del sistema. Ver `.env.example`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


@dataclass(frozen=True)
class Settings:
    """Valores de configuración inmutables."""

    # --- Redis (no relacional) ------------------------------------------------
    # Si REDIS_URL está definida tiene prioridad sobre host/port/password.
    redis_url: str | None
    redis_host: str
    redis_port: int
    redis_username: str
    redis_password: str | None
    redis_ssl: bool

    # Prefijo global de claves (namespace). Permite compartir una instancia.
    key_prefix: str

    # Tiempos de expiración (segundos).
    session_ttl: int
    hold_ttl: int
    cache_ttl: int

    # Tamaño máximo del feed de actividad (LTRIM).
    activity_max: int

    # --- PostgreSQL (relacional) ----------------------------------------------
    # Cadena de conexión del usuario de la APLICACIÓN (rol tf_app, mínimo privilegio).
    database_url: str | None
    pg_pool_min: int
    pg_pool_max: int
    pg_connect_timeout: int

    # --- Seguridad --------------------------------------------------------------
    login_max_attempts: int      # intentos fallidos permitidos
    login_block_seconds: int     # ventana/bloqueo (Redis INCR + EXPIRE)

    @property
    def connection_kwargs(self) -> dict:
        """Argumentos para construir el cliente redis-py."""
        base = {"decode_responses": True, "socket_timeout": 5, "socket_connect_timeout": 5}
        if self.redis_url:
            return {"url": self.redis_url, **base}
        kwargs = {
            "host": self.redis_host,
            "port": self.redis_port,
            "username": self.redis_username,
            "password": self.redis_password,
            **base,
        }
        if self.redis_ssl:
            kwargs["ssl"] = True
        return kwargs


def load_settings() -> Settings:
    """Construye `Settings` a partir del entorno."""
    return Settings(
        redis_url=(os.getenv("REDIS_URL") or "").strip() or None,
        redis_host=os.getenv("REDIS_HOST", "localhost"),
        redis_port=_int_env("REDIS_PORT", 6379),
        redis_username=os.getenv("REDIS_USERNAME", "default"),
        redis_password=os.getenv("REDIS_PASSWORD") or None,
        redis_ssl=os.getenv("REDIS_SSL", "false").lower() in {"1", "true", "yes"},
        key_prefix=os.getenv("KEY_PREFIX", "tf"),
        session_ttl=_int_env("SESSION_TTL_SECONDS", 1800),
        hold_ttl=_int_env("HOLD_TTL_SECONDS", 600),
        cache_ttl=_int_env("CACHE_TTL_SECONDS", 60),
        activity_max=_int_env("ACTIVITY_MAX", 100),
        database_url=(os.getenv("DATABASE_URL") or "").strip() or None,   # strip: al pegar secretos suele colarse un salto de línea
        pg_pool_min=_int_env("PG_POOL_MIN", 1),
        pg_pool_max=_int_env("PG_POOL_MAX", 10),
        pg_connect_timeout=_int_env("PG_CONNECT_TIMEOUT", 10),
        login_max_attempts=_int_env("LOGIN_MAX_ATTEMPTS", 5),
        login_block_seconds=_int_env("LOGIN_BLOCK_SECONDS", 300),
    )


settings = load_settings()
