"""Modelos Pydantic usados por la API (validación de entrada y salida)."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class EventIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    venue: str = Field(min_length=1, max_length=120)
    date: str = Field(description="Fecha ISO 8601, ej. 2026-12-05T20:00")
    category: str = Field(min_length=1, max_length=40)
    price: float = Field(gt=0)
    capacity: int = Field(gt=0)


class EventUpdate(BaseModel):
    name: str | None = None
    venue: str | None = None
    date: str | None = None
    category: str | None = None
    price: float | None = Field(default=None, gt=0)


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
    unique_visitors: int = 0


class UserIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: EmailStr


class UserOut(BaseModel):
    id: str
    name: str
    email: str
    created_at: str


class LoginIn(BaseModel):
    user_id: str


class SessionOut(BaseModel):
    token: str
    user_id: str
    ttl_seconds: int


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
