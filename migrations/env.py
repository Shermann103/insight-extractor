"""
env.py — Entorno de ejecución de Alembic.

Toma la URL de la base de datos de las variables de entorno (las mismas que usa
la app) y usa el metadata de nuestros modelos SQLAlchemy como referencia para
las migraciones.
"""

import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# Hacer importable el módulo de modelos.
sys.path.insert(0, os.path.join(os.getcwd(), "src", "data"))
sys.path.insert(0, os.path.join(os.getcwd(), "data"))

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Construir la URL desde las variables de entorno.
POSTGRES_USER = os.getenv("POSTGRES_USER", "insight")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "insight_pass")
POSTGRES_DB = os.getenv("POSTGRES_DB", "insight_db")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
DATABASE_URL = (
    f"postgresql+psycopg2://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
    f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
)
config.set_main_option("sqlalchemy.url", DATABASE_URL)

# Metadata de nuestros modelos, para autogeneración y validación.
from models import Base  # noqa: E402

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Migraciones en modo 'offline' (genera SQL sin conectarse)."""
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Migraciones en modo 'online' (conectándose a la base)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
