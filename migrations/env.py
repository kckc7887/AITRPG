from alembic import context
from sqlalchemy import engine_from_config
from sqlalchemy import pool

from aitrpg.adapters.storage import Base

CONFIG = context.config
TARGET_METADATA = Base.metadata


def migrate():
    if context.is_offline_mode():
        context.configure(
            url=CONFIG.get_main_option('sqlalchemy.url'),
            target_metadata=TARGET_METADATA,
            literal_binds=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = engine_from_config(
        CONFIG.get_section(CONFIG.config_ini_section),
        prefix='sqlalchemy.',
        poolclass=pool.NullPool,
    )
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=TARGET_METADATA
        )
        with context.begin_transaction():
            context.run_migrations()


migrate()
