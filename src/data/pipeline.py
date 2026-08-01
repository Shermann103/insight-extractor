"""
pipeline.py — Orquestación de la ingesta con gobernanza.

Flujo:
  1. Recibe registros crudos (de una fuente simulada: lista de dicts).
  2. Los valida con Pydantic (validator.py). Los inválidos se descartan y cuentan.
  3. Actualiza dimensiones con SCD tipo 2 (tarifas de canal).
  4. Consolida ventas por (fecha, canal) y las carga a la tabla de hechos,
     evitando duplicados (UPSERT sobre la restricción UNIQUE).

Devuelve un resumen con conteos, útil para el endpoint /data/ingest.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from models import (
    DimChannel,
    FactCampaignPerformance,
    SessionLocal,
)
from validator import RawChannelRate, RawSalesRecord


# ---------------------------------------------------------------------------
# SCD tipo 2: alta/actualización de tarifas de canal sin sobrescribir el pasado
# ---------------------------------------------------------------------------
def upsert_channel_rate(session, rate: RawChannelRate) -> None:
    """
    Aplica SCD tipo 2 sobre dim_channel.

    Si llega una tarifa nueva para un canal que ya tiene versión vigente:
      - cierra la versión vigente (valid_to = día anterior, is_current=False),
      - inserta la nueva versión como vigente.
    Si el CPC no cambió, no hace nada (evita versiones redundantes).
    """
    current = session.execute(
        select(DimChannel).where(
            DimChannel.channel_code == rate.channel,
            DimChannel.is_current.is_(True),
        )
    ).scalar_one_or_none()

    if current is not None and float(current.base_cpc) == float(rate.base_cpc):
        return  # sin cambios: no se crea versión nueva

    if current is not None:
        # Cerrar la versión vigente el día antes de que empiece la nueva.
        current.is_current = False
        current.valid_to = rate.valid_from

    session.add(
        DimChannel(
            channel_code=rate.channel,
            display_name=rate.channel.title(),
            base_cpc=rate.base_cpc,
            valid_from=rate.valid_from,
            valid_to=None,
            is_current=True,
        )
    )


def ensure_channel_exists(session, channel_code: str) -> int:
    """
    Garantiza que exista una fila de canal vigente para poder referenciarla
    desde la tabla de hechos. Si no existe, crea una con CPC por defecto.
    Devuelve el id del canal vigente.
    """
    ch = session.execute(
        select(DimChannel).where(
            DimChannel.channel_code == channel_code,
            DimChannel.is_current.is_(True),
        )
    ).scalar_one_or_none()

    if ch is None:
        ch = DimChannel(
            channel_code=channel_code,
            display_name=channel_code.title(),
            base_cpc=0,
            valid_from=date.today(),
            valid_to=None,
            is_current=True,
        )
        session.add(ch)
        session.flush()  # para obtener el id
    return ch.id


# ---------------------------------------------------------------------------
# Ingesta principal
# ---------------------------------------------------------------------------
def ingest_sales(raw_records: list[dict[str, Any]]) -> dict[str, int]:
    """
    Ingesta un lote de registros de ventas crudos con gobernanza completa.

    Devuelve un resumen: recibidos, validos, rechazados, filas de hechos afectadas.
    """
    valid: list[RawSalesRecord] = []
    rejected = 0

    # --- Paso 1: validación (gobernanza de consistencia + unificación) ---
    for rec in raw_records:
        try:
            valid.append(RawSalesRecord(**rec))
        except ValidationError:
            rejected += 1

    # --- Paso 2: consolidación por (fecha, canal) ---
    # Agrupamos en memoria antes de tocar la BD para minimizar escrituras.
    buckets: dict[tuple[date, str], dict[str, float]] = defaultdict(
        lambda: {
            "total_sales_amount": 0.0,
            "num_transactions": 0,
            "num_new_customers": 0,
            "num_clicks": 0,
        }
    )
    for r in valid:
        b = buckets[(r.event_date, r.channel)]
        b["total_sales_amount"] += r.amount
        b["num_transactions"] += 1
        b["num_new_customers"] += 1 if r.is_new_customer else 0
        b["num_clicks"] += r.clicks

    # --- Paso 3: carga a la tabla de hechos con anti-duplicados (UPSERT) ---
    affected = 0
    with SessionLocal() as session:
        for (event_date, channel_code), agg in buckets.items():
            channel_id = ensure_channel_exists(session, channel_code)

            stmt = pg_insert(FactCampaignPerformance).values(
                event_date=event_date,
                channel_id=channel_id,
                total_sales_amount=agg["total_sales_amount"],
                marketing_spend=0,  # la inversión puede venir de otra fuente
                num_transactions=int(agg["num_transactions"]),
                num_new_customers=int(agg["num_new_customers"]),
                num_clicks=int(agg["num_clicks"]),
            )
            # Si ya existe la combinación (event_date, channel_id), en lugar de
            # duplicar, ACUMULA sobre lo existente. Esto es la deduplicación.
            stmt = stmt.on_conflict_do_update(
                constraint="uq_fact_date_channel",
                set_={
                    "total_sales_amount": FactCampaignPerformance.total_sales_amount
                    + agg["total_sales_amount"],
                    "num_transactions": FactCampaignPerformance.num_transactions
                    + int(agg["num_transactions"]),
                    "num_new_customers": FactCampaignPerformance.num_new_customers
                    + int(agg["num_new_customers"]),
                    "num_clicks": FactCampaignPerformance.num_clicks
                    + int(agg["num_clicks"]),
                },
            )
            session.execute(stmt)
            affected += 1

        session.commit()

    return {
        "received": len(raw_records),
        "valid": len(valid),
        "rejected": rejected,
        "fact_rows_affected": affected,
    }


def ingest_channel_rates(raw_rates: list[dict[str, Any]]) -> dict[str, int]:
    """Ingesta tarifas de canal aplicando SCD tipo 2. Devuelve conteos."""
    valid: list[RawChannelRate] = []
    rejected = 0
    for rec in raw_rates:
        try:
            valid.append(RawChannelRate(**rec))
        except ValidationError:
            rejected += 1

    with SessionLocal() as session:
        for rate in valid:
            upsert_channel_rate(session, rate)
        session.commit()

    return {"received": len(raw_rates), "valid": len(valid), "rejected": rejected}
