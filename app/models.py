"""Modelos Pydantic usados por la API (validación de entrada y salida)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator


def _check_date(value: str) -> str:
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Usa el formato ISO 8601, p. ej. 2026-12-05T20:00") from exc
    return value


class EventIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    venue: str = Field(min_length=1, max_length=120)
    date: str = Field(description="Fecha ISO 8601, ej. 2026-12-05T20:00")
    category: str = Field(min_length=1, max_length=40)
    price: float = Field(gt=0)
    capacity: int = Field(gt=0)

    _date = field_validator("date")(_check_date)


class EventUpdate(BaseModel):
    name: str | None = None
    venue: str | None = None
    date: str | None = None
    category: str | None = None
    price: float | None = Field(default=None, gt=0)
    version: int | None = Field(default=None, description="Versión leída: activa el bloqueo optimista")

    @field_validator("date")
    @classmethod
    def _date(cls, value):
        return value if value is None else _check_date(value)


class EventOut(BaseModel):
    id: str
    name: str
    venue: str
    date: str
    category: str
    price: float
    capacity: int
    stock: int
    sold: int = 0
    version: int = 1
    unique_visitors: int = 0


class RegisterIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    id: str
    name: str
    email: str
    role: str
    active: bool = True
    created_at: str


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class SessionOut(BaseModel):
    token: str
    user_id: str
    role: str
    ttl_seconds: int
    user: UserOut


class HoldIn(BaseModel):
    user_id: str
    event_id: str
    qty: int = Field(gt=0, le=10)


class HoldOut(BaseModel):
    id: str
    user_id: str
    event_id: str
    qty: int
    created_at: str
    expires_at: str
    ttl_seconds: int


class OrderOut(BaseModel):
    id: str
    user_id: str
    event_id: str
    qty: int
    unit_price: float
    total: float
    created_at: str
    status: str


class RankingEntry(BaseModel):
    event_id: str
    name: str
    sold: int
