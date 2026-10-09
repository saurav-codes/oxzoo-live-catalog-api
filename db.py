"""Database and cache wiring: SQLAlchemy 2 models over DATABASE_URL and a
read-through Redis cache over REDIS_URL."""

import json
import logging
import os
from datetime import datetime, timezone
from functools import cache

import redis
from sqlalchemy import DateTime, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

log = logging.getLogger("catalog")
CACHE_TTL_S = 30


def database_url() -> str:
    # ox provides postgres://; SQLAlchemy wants a driver in the scheme.
    url = os.environ["DATABASE_URL"]
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


class Base(DeclarativeBase):
    pass


class Item(Base):
    __tablename__ = "items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(String(16), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    price_cents: Mapped[int] = mapped_column(Integer)
    stock: Mapped[int] = mapped_column(Integer)

    def as_dict(self) -> dict:
        return {"sku": self.sku, "name": self.name, "price_cents": self.price_cents, "stock": self.stock}


class Hop(Base):
    __tablename__ = "hops"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trace: Mapped[str] = mapped_column(String(36), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    step: Mapped[str] = mapped_column(String(40))
    detail: Mapped[str] = mapped_column(Text, default="")


@cache
def engine():
    return create_engine(database_url(), pool_size=5, max_overflow=5, pool_pre_ping=True, pool_timeout=5,
                         connect_args={"connect_timeout": 5, "options": "-c statement_timeout=5000"})


@cache
def cache_client() -> redis.Redis:
    return redis.Redis.from_url(os.environ["REDIS_URL"], socket_timeout=2, socket_connect_timeout=2)


def session() -> Session:
    return Session(engine())


def _cached(key: str, load):
    """Read-through: the cached JSON when present, else load() and store it
    for CACHE_TTL_S. A cache outage serves from Postgres and says so."""
    try:
        raw = cache_client().get(key)
        if raw is not None:
            return json.loads(raw), "hit"
    except redis.RedisError as e:
        log.warning("cache read failed: %s", e)
        return load(), "unavailable"
    value = load()
    try:
        cache_client().set(key, json.dumps(value), ex=CACHE_TTL_S)
    except redis.RedisError as e:
        log.warning("cache write failed: %s", e)
    return value, "miss"


def list_items() -> tuple[list[dict], str]:
    def load():
        with session() as s:
            return [i.as_dict() for i in s.scalars(select(Item).order_by(Item.id).limit(200))]
    return _cached("catalog:items", load)


def get_item(sku: str) -> tuple[dict | None, str]:
    def load():
        with session() as s:
            item = s.scalar(select(Item).where(Item.sku == sku))
            return item.as_dict() if item else None
    return _cached(f"catalog:item:{sku}", load)


def record_hop(trace: str, step: str, detail: str) -> None:
    with session() as s, s.begin():
        s.add(Hop(trace=trace, step=step, detail=detail[:200]))


def hops(trace: str) -> list[Hop]:
    with session() as s:
        return list(s.scalars(select(Hop).where(Hop.trace == trace).order_by(Hop.at, Hop.id).limit(50)))
