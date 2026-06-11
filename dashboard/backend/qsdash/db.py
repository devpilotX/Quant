"""SQLAlchemy engine/session plumbing.

Timestamps are stored timezone-naive and mean IST exchange time, matching the
quantsys core contract (core/types.py). The single exception is auth/audit
rows, which also carry naive timestamps produced by ``now_ist()`` so the whole
database speaks one clock.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from qsdash.config import settings

IST = timezone(timedelta(hours=5, minutes=30))


def now_ist() -> datetime:
    """Naive IST wall-clock — the one timestamp convention in the DB."""
    return datetime.now(IST).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def get_session() -> Session:
    return SessionLocal()
