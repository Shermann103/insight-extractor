"""
agent.py — Agente conversacional con LangGraph.

Grafo de estados con tres nodos lógicos (los que pide la prueba):

  1. classify_intent  -> decide: 'data' | 'analysis' | 'update'
  2. text_to_sql      -> traduce lenguaje natural a SQL (few-shot) y lo ejecuta
                         de forma segura (lista blanca + usuario read-only)
  3. reason           -> analiza los datos y produce diagnóstico + JSON

Ruteo:
  START -> classify_intent -> (data|analysis) -> text_to_sql -> reason -> END
                           -> (update)         -> reason -> END

El LLM es local vía Ollama (ChatOllama), sin API key.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Literal, TypedDict

from langchain_ollama import ChatOllama
from langgraph.graph import END, START, StateGraph

from tools import SQLValidationError, run_readonly_sql

# ---------------------------------------------------------------------------
# LLM local (Ollama)
# ---------------------------------------------------------------------------
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

llm = ChatOllama(model=OLLAMA_MODEL, base_url=OLLAMA_BASE_URL, temperature=0)


# ---------------------------------------------------------------------------
# Estado del grafo
# ---------------------------------------------------------------------------
class AgentState(TypedDict, total=False):
    """Estado que viaja entre nodos. Cada nodo lee y actualiza campos."""
    question: str                 # pregunta del usuario
    intent: str                   # 'data' | 'analysis' | 'update'
    sql: str                      # SQL generado
    data: dict[str, Any]          # resultado de la consulta
    error: str                    # mensaje de error si algo falla
    answer: str                   # respuesta final en lenguaje natural
    result_json: dict[str, Any]   # respuesta final estructurada (JSON)


# ---------------------------------------------------------------------------
# Esquema de la BD que se le muestra al modelo (few-shot / contexto)
# ---------------------------------------------------------------------------
DB_SCHEMA = """
Tablas disponibles (solo lectura):

- vw_campaign_kpis(event_date, channel_code, total_sales_amount, marketing_spend,
    num_transactions, num_new_customers, num_clicks, roi, cac, conversion_rate)
- fact_campaign_performance(event_date, channel_id, total_sales_amount,
    marketing_spend, num_transactions, num_new_customers, num_clicks)
- dim_channel(channel_code, display_name, base_cpc, valid_from, valid_to, is_current)
- dim_customer(customer_code, segment, city, valid_from, valid_to, is_current)
"""

# Ejemplos few-shot: anclan al modelo a SQL correcto y reducen alucinaciones.
FEW_SHOT = """
Ejemplos:
P: ¿Cuál es el ROI por canal?
SQL: SELECT channel_code, roi FROM vw_campaign_kpis;

P: ¿Qué canal tuvo más ventas?
SQL: SELECT channel_code, SUM(total_sales_amount) AS ventas FROM vw_campaign_kpis GROUP BY channel_code ORDER BY ventas DESC;

P: Muéstrame las tarifas vigentes de los canales.
SQL: SELECT channel_code, base_cpc FROM dim_channel WHERE is_current = true;
"""


# ---------------------------------------------------------------------------
# Nodo 1: clasificación de intención
# ---------------------------------------------------------------------------
def classify_intent(state: AgentState) -> AgentState:
    """Clasifica la pregunta en 'data', 'analysis' o 'update'."""
    prompt = (
        "Clasifica la intención del usuario en UNA palabra:\n"
        "- 'data': pide datos duros o cifras concretas.\n"
        "- 'analysis': pide análisis estratégico, recomendaciones o diagnóstico.\n"
        "- 'update': pide actualizar o ingestar datos.\n\n"
        f"Pregunta: {state['question']}\n"
        "Responde SOLO con: data, analysis o update."
    )
    resp = llm.invoke(prompt).content.strip().lower()
    intent = "analysis"
    for cand in ("data", "analysis", "update"):
        if cand in resp:
            intent = cand
            break
    return {"intent": intent}


def route_after_intent(state: AgentState) -> Literal["text_to_sql", "reason"]:
    """Ruteo condicional según la intención."""
    if state.get("intent") == "update":
        return "reason"  # las actualizaciones se manejan por el endpoint de ingesta
    return "text_to_sql"


# ---------------------------------------------------------------------------
# Nodo 2: Text-to-SQL controlado
# ---------------------------------------------------------------------------
def _extract_sql(raw: str) -> str:
    """Extrae la sentencia SQL de la respuesta del modelo."""
    # Quita fences ```sql ... ```
    fenced = re.search(r"```(?:sql)?\s*(.+?)```", raw, re.S | re.I)
    candidate = fenced.group(1) if fenced else raw
    # Toma desde el primer SELECT/WITH
    m = re.search(r"(select|with)\b.+", candidate, re.S | re.I)
    return (m.group(0) if m else candidate).strip().rstrip(";").strip()


def text_to_sql(state: AgentState) -> AgentState:
    """Traduce la pregunta a SQL y la ejecuta de forma segura."""
    prompt = (
        "Eres un experto en SQL de PostgreSQL. Genera UNA consulta SELECT que "
        "responda la pregunta. Usa solo las tablas del esquema. No expliques, "
        "responde solo con el SQL.\n"
        f"{DB_SCHEMA}\n{FEW_SHOT}\n"
        f"Pregunta: {state['question']}\nSQL:"
    )
    raw = llm.invoke(prompt).content
    sql = _extract_sql(raw)

    try:
        data = run_readonly_sql(sql)
        return {"sql": sql, "data": data}
    except SQLValidationError as e:
        return {"sql": sql, "error": f"SQL rechazado por gobernanza: {e}"}
    except Exception as e:  # error de ejecución (sintaxis, etc.)
        return {"sql": sql, "error": f"Error ejecutando SQL: {e}"}


# ---------------------------------------------------------------------------
# Nodo 3: razonamiento y recomendación
# ---------------------------------------------------------------------------
# Los KPIs pueden venir NULL cuando falta un insumo para calcularlos, no porque
# el resultado sea "malo". Este mapa explica qué falta cuando cada KPI es NULL.
KPI_NULL_MEANING: dict[str, str] = {
    "roi": "falta registrar la inversión (marketing_spend) para poder calcular el ROI",
    "cac": "no hay clientes nuevos registrados, por lo que no se puede calcular el CAC",
    "conversion_rate": "faltan clics registrados para calcular la tasa de conversión",
}


def detect_missing_data(data: dict[str, Any]) -> list[str]:
    """
    Revisa las filas devueltas y detecta KPIs en NULL, devolviendo notas
    legibles sobre qué insumo falta. Así el diagnóstico distingue entre
    'dato faltante' y 'resultado negativo'.
    """
    notes: set[str] = set()
    for row in data.get("rows", []):
        for col, value in row.items():
            if value is None and col in KPI_NULL_MEANING:
                notes.add(f"'{col}': {KPI_NULL_MEANING[col]}")
    return sorted(notes)


def reason(state: AgentState) -> AgentState:
    """Genera el diagnóstico de negocio en texto + JSON estructurado."""
    if state.get("error"):
        answer = (
            "No pude consultar los datos de forma segura. "
            f"Detalle: {state['error']}"
        )
        return {
            "answer": answer,
            "result_json": {"status": "error", "detail": state["error"]},
        }

    if state.get("intent") == "update":
        answer = (
            "Las actualizaciones de datos se realizan por el endpoint "
            "/data/ingest, que aplica la gobernanza. El agente es de solo lectura."
        )
        return {
            "answer": answer,
            "result_json": {"status": "info", "action": "use_ingest_endpoint"},
        }

    data = state.get("data", {})
    missing = detect_missing_data(data)

    # Nota explícita para el modelo sobre cómo interpretar los valores nulos.
    if missing:
        missing_note = (
            "IMPORTANTE: algunos valores aparecen como null (None). Un null NO "
            "significa un mal resultado ni un problema de negocio: significa que "
            "falta un dato de entrada para calcular ese KPI. Trátalo como 'dato "
            "faltante por completar', no como un ROI de cero ni una crisis. "
            "Detalle de qué falta:\n- " + "\n- ".join(missing) + "\n"
            "Recomienda completar esos datos de entrada; no inventes cifras."
        )
    else:
        missing_note = (
            "Todos los KPIs tienen valor. Analiza las cifras tal cual."
        )

    prompt = (
        "Eres un analista de negocio. Con base en estos datos de campañas, "
        "escribe un diagnóstico breve con recomendaciones de optimización.\n\n"
        f"{missing_note}\n\n"
        f"Pregunta: {state['question']}\n"
        f"Datos: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
        "Sé concreto y accionable. No interpretes un null como un valor de cero."
    )
    answer = llm.invoke(prompt).content.strip()

    return {
        "answer": answer,
        "result_json": {
            "status": "ok",
            "intent": state.get("intent"),
            "sql": state.get("sql"),
            "data": data,
            "missing_inputs": missing,
            "diagnosis": answer,
        },
    }


# ---------------------------------------------------------------------------
# Construcción del grafo
# ---------------------------------------------------------------------------
def build_agent():
    """Construye y compila el grafo del agente."""
    graph = StateGraph(AgentState)

    graph.add_node("classify_intent", classify_intent)
    graph.add_node("text_to_sql", text_to_sql)
    graph.add_node("reason", reason)

    graph.add_edge(START, "classify_intent")
    graph.add_conditional_edges(
        "classify_intent",
        route_after_intent,
        {"text_to_sql": "text_to_sql", "reason": "reason"},
    )
    graph.add_edge("text_to_sql", "reason")
    graph.add_edge("reason", END)

    return graph.compile()


# Instancia lista para importar desde la API.
agent_app = build_agent()


def run_agent(question: str) -> dict[str, Any]:
    """Punto de entrada simple: recibe una pregunta, devuelve el estado final."""
    final = agent_app.invoke({"question": question})
    return {
        "answer": final.get("answer", ""),
        "result_json": final.get("result_json", {}),
    }


if __name__ == "__main__":
    import sys

    q = sys.argv[1] if len(sys.argv) > 1 else "¿Cuál es el ROI por canal?"
    out = run_agent(q)
    print(out["answer"])
    print("---JSON---")
    print(json.dumps(out["result_json"], ensure_ascii=False, indent=2, default=str))
