import os
from collections.abc import Generator
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy.engine import Engine, make_url
from sqlmodel import Session, SQLModel, create_engine

_engine: Engine | None = None


def database_url() -> str:
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL environment variable is required. "
            "On Railway, reference your Postgres service's DATABASE_URL variable."
        )

    # Railway and some providers use postgres://; SQLAlchemy + psycopg2 expect postgresql.
    parsed = make_url(url)
    if parsed.drivername == "postgres":
        parsed = parsed.set(drivername="postgresql+psycopg2")
    elif parsed.drivername == "postgresql":
        parsed = parsed.set(drivername="postgresql+psycopg2")
    return parsed.render_as_string(hide_password=False)


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(database_url(), pool_pre_ping=True)
    return _engine


class _LazyEngine:
    """Defers engine creation until first use (after env vars are available)."""

    def __getattr__(self, name: str) -> Any:
        return getattr(get_engine(), name)

    def __repr__(self) -> str:
        return repr(get_engine())


engine = _LazyEngine()


def get_session() -> Generator[Session, None, None]:
    with Session(get_engine()) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def init_db() -> None:
    # Import models so SQLModel registers all tables before create_all.
    import models  # noqa: F401

    SQLModel.metadata.create_all(get_engine())
