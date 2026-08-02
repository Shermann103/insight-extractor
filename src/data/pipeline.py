"""
pipeline.py — Orquestación de la ingesta con gobernanza (multi-fuente).

Fuentes de datos:
  - Fuente A (PostgreSQL / transaccional): ventas diarias -> ingest_sales().
  - Fuente B (archivo estructurado CSV o API/JSON): inversión de marketing
    por día y canal -> ingest_spend_from_csv() / ingest_spend_from_api().

Flujo general:
  1. Recibe registros crudos de cualquier fuente.
  2. Los valida con Pydantic (validator.py). Los inválidos se descartan y cuentan.
  3. Actualiza dimensiones con SCD tipo 2 (tarifas de canal).
  4. Consolida por (fecha, canal) y carga/actualiza la tabla de hechos con
     UPSERT (evita duplicados). Las ventas y la inversión se unifican sobre la
     misma fila (fecha, canal); los clics de ambas fuentes se suman.

Devuelve un resumen con conteos, útil para el endpoint /data/ingest.
"""

from __future__ import annotations

import csv
import io
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
from validator import RawChannelRate, RawSalesRecord, RawSpendRecord


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


# ---------------------------------------------------------------------------
# FUENTE B: inversión de marketing (CSV / API), unificada sobre la tabla de hechos
# ---------------------------------------------------------------------------
def _ingest_spend_records(raw_records: list[dict[str, Any]]) -> dict[str, int]:
    """
    Lógica común de ingesta de inversión, sin importar el formato de origen.

    Valida con RawSpendRecord (misma gobernanza), consolida por (fecha, canal) y
    fusiona sobre la tabla de hechos:
      - marketing_spend e impresiones/impressions: se ACTUALIZAN (la inversión es
        el dato autoritativo de esta fuente).
      - num_clicks: se SUMA a los clics ya existentes (clics de ventas + de ads).
    """
    valid: list[RawSpendRecord] = []
    rejected = 0
    for rec in raw_records:
        try:
            valid.append(RawSpendRecord(**rec))
        except ValidationError:
            rejected += 1

    # Consolidar por (fecha, canal)
    buckets: dict[tuple[date, str], dict[str, float]] = defaultdict(
        lambda: {"spend": 0.0, "clicks": 0, "impressions": 0}
    )
    for r in valid:
        b = buckets[(r.event_date, r.channel)]
        b["spend"] += r.spend
        b["clicks"] += r.clicks
        b["impressions"] += r.impressions

    affected = 0
    with SessionLocal() as session:
        for (event_date, channel_code), agg in buckets.items():
            channel_id = ensure_channel_exists(session, channel_code)

            stmt = pg_insert(FactCampaignPerformance).values(
                event_date=event_date,
                channel_id=channel_id,
                total_sales_amount=0,
                marketing_spend=agg["spend"],
                num_transactions=0,
                num_new_customers=0,
                num_clicks=int(agg["clicks"]),
            )
            # Si la fila (fecha, canal) ya existe:
            #   - la inversión y las impresiones se SUMAN (una misma celda puede
            #     recibir gasto desde varias fuentes: CSV + API),
            #   - los clics también se SUMAN (clics de ventas + de ads).
            stmt = stmt.on_conflict_do_update(
                constraint="uq_fact_date_channel",
                set_={
                    "marketing_spend": FactCampaignPerformance.marketing_spend
                    + agg["spend"],
                    "num_clicks": FactCampaignPerformance.num_clicks
                    + int(agg["clicks"]),
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


def ingest_spend_from_csv(csv_content: str) -> dict[str, int]:
    """
    Ingesta inversión desde un CSV (archivo estructurado).

    Columnas esperadas: date, channel, spend, impressions, clicks.
    Se pasa el contenido del archivo como texto para no acoplar a rutas.
    """
    reader = csv.DictReader(io.StringIO(csv_content))
    records: list[dict[str, Any]] = []
    for row in reader:
        records.append(
            {
                "event_date": row.get("date", "").strip(),
                "channel": row.get("channel", "").strip(),
                "spend": float(row.get("spend", 0) or 0),
                "impressions": int(row.get("impressions", 0) or 0),
                "clicks": int(row.get("clicks", 0) or 0),
            }
        )
    result = _ingest_spend_records(records)
    result["source"] = "csv"
    return result


def ingest_spend_from_api(api_payload: list[dict[str, Any]]) -> dict[str, int]:
    """
    Ingesta inversión desde una respuesta de API simulada (lista de objetos JSON).

    Cada objeto debe traer: date/event_date, channel, spend, impressions, clicks.
    Acepta tanto 'date' como 'event_date' para ser flexible con distintas APIs.
    """
    records: list[dict[str, Any]] = []
    for item in api_payload:
        records.append(
            {
                "event_date": item.get("event_date") or item.get("date"),
                "channel": item.get("channel"),
                "spend": item.get("spend", 0),
                "impressions": item.get("impressions", 0),
                "clicks": item.get("clicks", 0),
            }
        )
    result = _ingest_spend_records(records)
    result["source"] = "api"
    return result
