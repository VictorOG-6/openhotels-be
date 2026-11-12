import os
from collections.abc import Generator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.engine import Engine, make_url
from sqlmodel import Session, SQLModel, create_engine

_engine: Engine | None = None

DATABASE_URL = os.getenv("DATABASE_URL")

def database_url() -> str:
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is required")

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


engine = get_engine()


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def init_db() -> None:
    # Import models so SQLModel registers all tables before create_all.
    import models  # noqa: F401

    SQLModel.metadata.create_all(engine)
