"""SQLModel engine and session management."""

from collections.abc import Iterator

from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.config import Settings, get_settings

_engine_cache: dict[str, Engine] = {}


def get_engine(settings: Settings) -> Engine:
    """Return a cached engine for the given settings' database URL.

    SQLite connections are created with `check_same_thread=False` because FastAPI's
    background tasks and the TestClient may use a different thread than the one that
    created the engine.
    """
    if settings.database_url not in _engine_cache:
        connect_args = (
            {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
        )
        _engine_cache[settings.database_url] = create_engine(
            settings.database_url, connect_args=connect_args
        )
    return _engine_cache[settings.database_url]


def init_db(settings: Settings) -> None:
    """Create the data directory and all tables. Safe to call repeatedly."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "jobs").mkdir(parents=True, exist_ok=True)
    engine = get_engine(settings)
    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a DB session bound to the current settings."""
    settings = get_settings()
    engine = get_engine(settings)
    with Session(engine) as session:
        yield session


def session_for(settings: Settings) -> Session:
    """Create a standalone session, for use outside the request/response cycle
    (e.g. the background pipeline)."""
    engine = get_engine(settings)
    return Session(engine)
