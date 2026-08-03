"""SQLModel engine and session management."""

from collections.abc import Iterator

from sqlalchemy import Engine, inspect, text
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


def _add_missing_columns(engine: Engine) -> None:
    """Add columns present in the SQLModel definitions but missing from the DB.

    Additive only (new nullable columns) — covers the common case of a model
    gaining a field during local development. Column drops, renames, or type
    changes still require resetting the dev DB by hand.
    """
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table_name, table in SQLModel.metadata.tables.items():
            if not inspector.has_table(table_name):
                continue
            existing_columns = {col["name"] for col in inspector.get_columns(table_name)}
            for column in table.columns:
                if column.name in existing_columns:
                    continue
                ddl_type = column.type.compile(dialect=engine.dialect)
                conn.execute(
                    text(f'ALTER TABLE "{table_name}" ADD COLUMN "{column.name}" {ddl_type}')
                )


def init_db(settings: Settings) -> None:
    """Create the data directory and all tables, then sync any new columns.

    Safe to call repeatedly.
    """
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "jobs").mkdir(parents=True, exist_ok=True)
    engine = get_engine(settings)
    SQLModel.metadata.create_all(engine)
    _add_missing_columns(engine)


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
