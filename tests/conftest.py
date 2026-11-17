"""Fixtures: usa fakeredis (con soporte Lua) para no depender de la nube en tests."""

from __future__ import annotations

import fakeredis
import pytest

from app.db import Database


@pytest.fixture
def db() -> Database:
    client = fakeredis.FakeRedis(decode_responses=True)
    return Database(client)
