"""
main.py — API FastAPI del sistema Insight-Extractor.

Expone tres endpoints (Fase 3):
  - POST /chat         → conversa con el agente de IA (LangGraph).
  - POST /data/ingest  → dispara la ingesta con gobernanza (fuentes A y B).
  - GET  /metrics      → devuelve los KPIs actuales calculados en la BD.

Notas de diseño:
  - Los módulos de datos e IA usan imports planos; para importarlos desde aquí,
    se añaden sus carpetas al sys.path al arrancar (evita reescribir código ya
    probado en las fases 1 y 2).
  - run_agent() es síncrona (Ollama bloquea). Para no congelar el bucle async de
    FastAPI, se ejecuta en un hilo aparte con run_in_threadpool.
"""

from __future__ import annotations

import os
import sys

# --- Hacer importables los módulos de src/data y src/ai ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE_DIR, "data"))
sys.path.insert(0, os.path.join(BASE_DIR, "ai"))
# Cuando se ejecuta desde la raíz del repo (desarrollo local):
sys.path.insert(0, os.path.join(BASE_DIR, "src", "data"))
sys.path.insert(0, os.path.join(BASE_DIR, "src", "ai"))

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from agent import run_agent  # noqa: E402
from pipeline import (  # noqa: E402
    ingest_channel_rates,
    ingest_sales,
    ingest_spend_from_api,
    ingest_spend_from_csv,
)
from tools import get_kpis  # noqa: E402

app = FastAPI(
    title="Insight-Extractor API",
    description="Ingesta gobernada, KPIs y agente de IA sobre campañas de marketing.",
    version="1.0.0",
)


# ---------------------------------------------------------------------------
# Modelos de entrada/salida (contratos de la API)
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    question: str


class ChatResponse(BaseModel):
    answer: str
    result_json: dict[str, Any]


class IngestRequest(BaseModel):
    """
    Cuerpo de /data/ingest. Todos los campos son opcionales: se ingesta lo que
    venga. Permite cargar ventas (Fuente A), tarifas de canal (SCD2) e inversión
    (Fuente B) en formato API/JSON o CSV (texto) en una sola llamada.
    """
    sales: list[dict[str, Any]] | None = None
    channel_rates: list[dict[str, Any]] | None = None
    spend_api: list[dict[str, Any]] | None = None
    spend_csv: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/")
def root() -> dict[str, str]:
    """Chequeo rápido de que la API está viva."""
    return {"status": "ok", "service": "insight-extractor"}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """
    Conversa con el agente de IA. Traduce la pregunta a SQL seguro, consulta la
    BD y devuelve un diagnóstico en texto + un JSON estructurado.

    El endpoint es asíncrono; como el agente (Ollama) es bloqueante, se ejecuta
    en un threadpool para no bloquear el servidor.
    """
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="La pregunta no puede estar vacía.")

    result = await run_in_threadpool(run_agent, req.question)
    return ChatResponse(answer=result["answer"], result_json=result["result_json"])


@app.post("/data/ingest")
async def data_ingest(req: IngestRequest) -> dict[str, Any]:
    """
    Dispara la ingesta con gobernanza. Procesa lo que venga en el cuerpo:
    ventas, tarifas de canal (SCD2) y/o inversión (API o CSV). Devuelve un
    resumen por fuente con conteos de recibidos/válidos/rechazados.
    """
    summary: dict[str, Any] = {}

    if req.channel_rates:
        summary["channel_rates"] = await run_in_threadpool(
            ingest_channel_rates, req.channel_rates
        )
    if req.sales:
        summary["sales"] = await run_in_threadpool(ingest_sales, req.sales)
    if req.spend_api:
        summary["spend_api"] = await run_in_threadpool(
            ingest_spend_from_api, req.spend_api
        )
    if req.spend_csv:
        summary["spend_csv"] = await run_in_threadpool(
            ingest_spend_from_csv, req.spend_csv
        )

    if not summary:
        raise HTTPException(
            status_code=400,
            detail="No se envió ninguna fuente de datos (sales, channel_rates, spend_api o spend_csv).",
        )

    return {"status": "ok", "ingested": summary}


@app.get("/metrics")
async def metrics(limit: int = 100) -> dict[str, Any]:
    """
    Devuelve los KPIs actuales (ROI, CAC, conversión) calculados directamente en
    la base de datos, leídos con el usuario de solo lectura. Sirve para
    contrastar con lo que responde el agente de IA.
    """
    data = await run_in_threadpool(get_kpis, limit)
    return {"status": "ok", "kpis": data}
