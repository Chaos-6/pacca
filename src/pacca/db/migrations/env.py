"""
Alembic migration environment configuration.

Supports both sync and async migrations for PostgreSQL and SQLite.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Import models to ensure they're registered with Base.metadata.
#
# PACCA has TWO SQLAlchemy declarative Bases (see CLAUDE.md "Where things
# live"): the domain Base (pacca.db.models.Base — authorization_requests,
# audit_logs, etc.) and the auth Base (pacca.api.database.Base — users).
# Alembic needs metadata from both to see every table; importing
# pacca.api.models.user registers User on the auth Base before we read its
# metadata below.
import pacca.api.models.user  # noqa: F401 — registers User on _AuthBase.metadata
from pacca.api.database import Base as _AuthBase
from pacca.config import get_settings
from pacca.db.models import Base as _DomainBase

# Alembic Config object
config = context.config

# Setup logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Set target metadata for autogenerate support. Alembic accepts a list of
# MetaData objects (combines them for diffing) — this is what makes `users`
# (previously invisible — it lived only on _AuthBase) visible to autogenerate.
target_metadata = [_DomainBase.metadata, _AuthBase.metadata]


def get_url() -> str:
    """Get database URL from settings."""
    settings = get_settings()
    return settings.database_url


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine,
    though an Engine is acceptable here as well. By skipping the Engine
    creation we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.
    """
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations with the given connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in async mode for PostgreSQL."""
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """
    Run migrations in 'online' mode.

    In this scenario we need to create an Engine and associate a
    connection with the context.
    """
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
