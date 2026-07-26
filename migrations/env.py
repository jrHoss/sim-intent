from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import LocalDataConfig
from app.persistence import Base

config = context.config
target_metadata = Base.metadata
if not config.get_main_option("sqlalchemy.url"):
    local_data = LocalDataConfig.from_env()
    local_data.root.mkdir(parents=True, exist_ok=True)
    config.set_main_option("sqlalchemy.url", local_data.database_url.replace("%", "%%"))


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_offline() if context.is_offline_mode() else run_migrations_online()
