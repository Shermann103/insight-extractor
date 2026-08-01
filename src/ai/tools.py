"""
tools.py — Herramientas del agente para consultar la base gobernada.

Contiene:
  - Conexión de SOLO LECTURA (usuario insight_ro).
  - run_readonly_sql(): ejecutor con lista blanca de tablas/columnas y
    guardas contra sentencias de escritura. Es la segunda capa de defensa
    (la primera es el propio usuario read-only en PostgreSQL).
  - get_kpis(): lee los KPIs directamente de la vista vw_campaign_kpis.

Estas funciones son las únicas puertas por las que el agente toca la base.
"""

from __future__ import annotations

import os
import re
from typing import Any

from sqlalchemy import create_engine, text

# ---------------------------------------------------------------------------
# Conexión de SOLO LECTURA
# ---------------------------------------------------------------------------
# Usa el rol insight_ro, que a nivel de PostgreSQL solo tiene permiso SELECT.
RO_USER = os.getenv("POSTGRES_RO_USER", "insight_ro")
RO_PASSWORD = os.getenv("POSTGRES_RO_PASSWORD", "readonly_pass")
DB = os.getenv("POSTGRES_DB", "insight_db")
HOST = os.getenv("POSTGRES_HOST", "localhost")
PORT = os.getenv("POSTGRES_PORT", "5432")

RO_DATABASE_URL = (
    f"postgresql+psycopg2://{RO_USER}:{RO_PASSWORD}@{HOST}:{PORT}/{DB}"
)

# El engine se crea una vez y se reutiliza.
ro_engine = create_engine(RO_DATABASE_URL, echo=False, future=True)


# ---------------------------------------------------------------------------
# Lista blanca (segunda capa de defensa, a nivel de aplicación)
# ---------------------------------------------------------------------------
# Solo se permite consultar estas tablas/vista. Cualquier consulta que
# referencie algo fuera de aquí se rechaza antes de tocar la base.
ALLOWED_TABLES: set[str] = {
    "fact_campaign_performance",
    "dim_channel",
    "dim_customer",
    "vw_campaign_kpis",
}

# Palabras prohibidas: cualquier intento de mutación o comando peligroso.
FORBIDDEN_KEYWORDS: set[str] = {
    "insert", "update", "delete", "drop", "alter", "truncate",
    "create", "grant", "revoke", "copy", "call", "do",
}


class SQLValidationError(Exception):
    """Se lanza cuando una consulta no pasa la lista blanca o las guardas."""


def _validate_sql(sql: str) -> None:
    """
    Valida una consulta SQL antes de ejecutarla. Lanza SQLValidationError
    si no cumple. Reglas:
      1. Debe ser una única sentencia (sin ';' múltiples).
      2. Debe empezar por SELECT o WITH.
      3. No puede contener palabras clave de escritura.
      4. Solo puede referenciar tablas de la lista blanca.
    """
    cleaned = sql.strip().rstrip(";").strip()
    lowered = cleaned.lower()

    # 1. Una sola sentencia
    if ";" in cleaned:
        raise SQLValidationError("Solo se permite una sentencia SQL.")

    # 2. Debe ser de lectura
    if not (lowered.startswith("select") or lowered.startswith("with")):
        raise SQLValidationError("Solo se permiten consultas SELECT / WITH.")

    # 3. Sin palabras de escritura (como palabras completas)
    tokens = set(re.findall(r"[a-z_]+", lowered))
    forbidden = tokens & FORBIDDEN_KEYWORDS
    if forbidden:
        raise SQLValidationError(f"Palabras no permitidas: {forbidden}")

    # 4. Toda tabla referenciada (tras FROM / JOIN) debe estar en la lista blanca
    referenced = re.findall(r"(?:from|join)\s+([a-z_][a-z0-9_]*)", lowered)
    for tbl in referenced:
        if tbl not in ALLOWED_TABLES:
            raise SQLValidationError(
                f"Tabla no permitida: '{tbl}'. Permitidas: {sorted(ALLOWED_TABLES)}"
            )


def run_readonly_sql(sql: str, limit: int = 100) -> dict[str, Any]:
    """
    Ejecuta una consulta de solo lectura tras validarla contra la lista blanca.

    Devuelve un dict con las columnas y filas (serializable a JSON).
    Aplica un LIMIT de seguridad si la consulta no trae uno.
    """
    _validate_sql(sql)

    cleaned = sql.strip().rstrip(";").strip()
    if "limit" not in cleaned.lower():
        cleaned = f"{cleaned} LIMIT {limit}"

    with ro_engine.connect() as conn:
        result = conn.execute(text(cleaned))
        columns = list(result.keys())
        rows = [dict(zip(columns, row)) for row in result.fetchall()]

    # Numeric/Decimal -> float para que sea JSON-serializable
    for row in rows:
        for k, v in row.items():
            if hasattr(v, "__float__") and not isinstance(v, (int, float, bool)):
                row[k] = float(v)

    return {"columns": columns, "rows": rows, "row_count": len(rows)}


def get_kpis(limit: int = 100) -> dict[str, Any]:
    """Lee los KPIs directamente de la vista gobernada."""
    return run_readonly_sql("SELECT * FROM vw_campaign_kpis", limit=limit)
