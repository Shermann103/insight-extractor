"""Esquema inicial: dimensiones SCD2, tabla de hechos y vista de KPIs.

Revision ID: 0001_initial
Revises:
Create Date: 2026-01-01

Esta migración crea todo el esquema de la Fase 1:
  - dim_channel y dim_customer (dimensiones con SCD tipo 2)
  - fact_campaign_performance (tabla de hechos, con UNIQUE anti-duplicados)
  - vw_campaign_kpis (vista que calcula ROI, CAC y conversión)

Las tablas se crean a partir del metadata de los modelos SQLAlchemy (una sola
fuente de verdad); la vista se crea con SQL explícito.
"""

import os
import sys
from typing import Sequence, Union

from alembic import op

# Importar el metadata de los modelos y el SQL de la vista.
sys.path.insert(0, os.path.join(os.getcwd(), "src", "data"))
sys.path.insert(0, os.path.join(os.getcwd(), "data"))
from models import KPI_VIEW_SQL, Base  # noqa: E402

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Crear todas las tablas definidas en los modelos.
    bind = op.get_bind()
    Base.metadata.create_all(bind)
    # Crear la vista de KPIs.
    op.execute(KPI_VIEW_SQL)

    # Crear el usuario de solo lectura para el agente de IA (idempotente).
    ro_user = os.getenv("POSTGRES_RO_USER", "insight_ro")
    ro_pass = os.getenv("POSTGRES_RO_PASSWORD", "readonly_pass")
    op.execute(
        f"""
        DO $$
        BEGIN
           IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '{ro_user}') THEN
              CREATE ROLE {ro_user} LOGIN PASSWORD '{ro_pass}';
           END IF;
        END
        $$;
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {ro_user}")
    op.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {ro_user}")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {ro_user}"
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS vw_campaign_kpis")
    bind = op.get_bind()
    Base.metadata.drop_all(bind)
