"""
models.py — Modelos SQLAlchemy del sistema Insight-Extractor.

Contiene:
  - Configuración de conexión (engine + session).
  - Dimensiones con historial SCD tipo 2: DimChannel (tarifas) y DimCustomer.
  - Tabla de hechos: FactCampaignPerformance.
  - SQL para la vista de KPIs (ROI, CAC, tasa de conversión).

Nota: en un proyecto final este archivo suele partirse (database.py, models/, etc.).
Se deja unificado para que sea fácil de leer durante la prueba.
"""

from __future__ import annotations

import os
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    create_engine,
    func,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    sessionmaker,
)

# ---------------------------------------------------------------------------
# Conexión a la base de datos
# ---------------------------------------------------------------------------
# Se construye la URL desde variables de entorno (.env). Con Docker Compose,
# dentro de la red de contenedores el host será "db"; en local, "localhost".
POSTGRES_USER = os.getenv("POSTGRES_USER", "insight")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "insight_pass")
POSTGRES_DB = os.getenv("POSTGRES_DB", "insight_db")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")

DATABASE_URL = (
    f"postgresql+psycopg2://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
    f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
)

engine = create_engine(DATABASE_URL, echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Clase base declarativa de la que heredan todos los modelos."""
    pass


# ---------------------------------------------------------------------------
# DIMENSIÓN 1: Canales, con historial SCD tipo 2 (tarifas que cambian en el tiempo)
# ---------------------------------------------------------------------------
class DimChannel(Base):
    """
    Dimensión de canal de marketing con SCD tipo 2.

    Cada cambio en el CPC (costo por clic) base de un canal genera una NUEVA fila
    en vez de sobrescribir la anterior. Así el histórico queda intacto.

    Ejemplo:
        channel_code='FACEBOOK', cpc=0.50, valid_from=2026-01-01, valid_to=2026-01-31, is_current=False
        channel_code='FACEBOOK', cpc=0.70, valid_from=2026-02-01, valid_to=NULL,       is_current=True
    """
    __tablename__ = "dim_channel"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Clave "de negocio": identifica la entidad lógica (el canal) a través de sus versiones.
    channel_code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)

    # Atributo que cambia en el tiempo: el costo por clic base.
    base_cpc: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)

    # --- Columnas SCD tipo 2 ---
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)  # NULL = vigente
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    facts: Mapped[list["FactCampaignPerformance"]] = relationship(
        back_populates="channel"
    )


# ---------------------------------------------------------------------------
# DIMENSIÓN 2: Clientes, con historial SCD tipo 2 (ej. cambia de segmento/ciudad)
# ---------------------------------------------------------------------------
class DimCustomer(Base):
    """
    Dimensión de cliente con SCD tipo 2.

    Guarda el histórico de atributos que cambian (segmento, ciudad). Si un cliente
    pasa de segmento 'STANDARD' a 'PREMIUM', se cierra la fila vieja y se abre una nueva.
    """
    __tablename__ = "dim_customer"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    customer_code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    segment: Mapped[str] = mapped_column(String(50), nullable=False)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # --- Columnas SCD tipo 2 ---
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ---------------------------------------------------------------------------
# TABLA DE HECHOS: desempeño consolidado por día y canal
# ---------------------------------------------------------------------------
class FactCampaignPerformance(Base):
    """
    Tabla de hechos consolidada: unifica ventas e inversión por día y canal.

    'Grano' de la tabla = una fila por (fecha, canal). Esa es la unidad mínima de
    análisis. La restricción UNIQUE(date, channel_id) evita duplicados en el histórico.
    """
    __tablename__ = "fact_campaign_performance"
    __table_args__ = (
        UniqueConstraint("event_date", "channel_id", name="uq_fact_date_channel"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    event_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("dim_channel.id"), nullable=False
    )

    # Métricas medibles ("hechos")
    total_sales_amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    marketing_spend: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    num_transactions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    num_new_customers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    num_clicks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    channel: Mapped["DimChannel"] = relationship(back_populates="facts")


# ---------------------------------------------------------------------------
# VISTA DE KPIs: ROI, CAC y tasa de conversión calculados desde la tabla de hechos
# ---------------------------------------------------------------------------
# Se crea como VIEW en SQL (no como tabla) para que los KPIs siempre reflejen
# los datos actuales sin duplicar información.
#
#   ROI               = (ventas - inversión) / inversión
#   CAC               = inversión / clientes nuevos
#   Tasa de conversión = transacciones / clics
#
# NULLIF(x, 0) evita la división por cero (devuelve NULL en vez de error).
KPI_VIEW_SQL = """
CREATE OR REPLACE VIEW vw_campaign_kpis AS
SELECT
    f.event_date,
    c.channel_code,
    f.total_sales_amount,
    f.marketing_spend,
    f.num_transactions,
    f.num_new_customers,
    f.num_clicks,
    ROUND(
        (f.total_sales_amount - f.marketing_spend) / NULLIF(f.marketing_spend, 0),
        4
    ) AS roi,
    ROUND(
        f.marketing_spend / NULLIF(f.num_new_customers, 0),
        2
    ) AS cac,
    ROUND(
        f.num_transactions::numeric / NULLIF(f.num_clicks, 0),
        4
    ) AS conversion_rate
FROM fact_campaign_performance f
JOIN dim_channel c ON c.id = f.channel_id;
"""


def init_db() -> None:
    """Crea todas las tablas y la vista de KPIs. Idempotente."""
    from sqlalchemy import text

    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text(KPI_VIEW_SQL))


if __name__ == "__main__":
    init_db()
    print("Tablas y vista de KPIs creadas correctamente.")
