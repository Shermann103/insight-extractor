# Insight-Extractor & History Tracker

Sistema automatizado que combina **ingeniería de datos y gobernanza** con un
**agente de IA (LangGraph)** para extraer, gobernar, modelar y analizar datos de
desempeño de campañas de marketing y ventas, exponiéndolo todo mediante una API
moderna (FastAPI).

> Estado del proyecto: **Fase 1 completa** (ingesta, gobernanza y modelado).
> Fase 2 (agente de IA) y Fase 3 (API y despliegue) en construcción.

---

## Cómo se aborda cada requisito

Esta sección mapea cada requisito de la prueba con la forma en que se resolvió y
la decisión de diseño detrás. El detalle técnico de cada punto está más abajo.

### Fase 1 — Ingesta, modelado y gobernanza

| Requisito de la prueba | Cómo se aborda | Decisión / porqué |
|---|---|---|
| **Unificar y estandarizar nombres de canal** ("FB Ads", "facebook_ads", "Facebook" → un valor estándar) | Mapa de equivalencias en `validator.py` que normaliza (minúsculas, sin espacios) y homologa a un valor canónico (`FACEBOOK`). | Se eligió un **diccionario de mapeo** por ser explícito, auditable y fácil de extender. Los canales no mapeados **no se descartan**: se estandarizan igual para no perder datos. |
| **Consistencia: no permitir montos negativos ni fechas futuras** | Validadores Pydantic (`field_validator` / `model_validator`) que rechazan esos registros. | Se usa **Pydantic** (sugerido por la prueba) por validación declarativa. La validación es **por registro**: un dato malo se descarta y se cuenta, sin abortar el lote. |
| **Evitar duplicados en el histórico** | UPSERT (`ON CONFLICT DO UPDATE`) contra una restricción `UNIQUE(event_date, channel_id)`. | Se garantiza la unicidad **a nivel de base de datos**, el lugar más robusto: aunque la app falle, la BD no admite duplicados. |
| **Tabla de hechos consolidada** (`fact_campaign_performance`) que unifique ventas e inversión por día y canal | Tabla con grano `(fecha, canal)` modelada en SQLAlchemy. | El grano fecha+canal permite consolidar y es la misma clave que sostiene la deduplicación. |
| **Historial / SCD (no sobrescribir el pasado)** | SCD **tipo 2** en `dim_channel` (tarifas) y `dim_customer`, con `valid_from`, `valid_to`, `is_current`. | Se implementó sobre **ambas** dimensiones para demostrar dominio del patrón. Un cambio cierra la versión vigente y abre una nueva; el pasado queda intacto. |
| **KPIs calculados: ROI, CAC, Tasa de Conversión** | Vista SQL `vw_campaign_kpis` que los calcula desde la tabla de hechos. | Se eligió **vista** (no tabla) para que los KPIs reflejen siempre el dato actual sin duplicar información. `NULLIF` evita división por cero. |

### Gobernanza en el consumo de IA (Fase 2)

| Requisito de la prueba | Cómo se aborda | Decisión / porqué |
|---|---|---|
| **Seguridad read-only del agente** (evitar mutaciones y SQL injection) | (1) Usuario PostgreSQL `insight_ro` con solo `SELECT`. (2) Lista blanca de tablas/columnas en la app. | **Defensa en profundidad**: si una capa falla, la otra protege. El candado a nivel de motor es infranqueable desde la app. |
| **Text-to-SQL sin alucinaciones** | Few-shot prompting + validación contra lista blanca antes de ejecutar. | Los ejemplos en el prompt anclan al modelo a consultas correctas; la lista blanca bloquea cualquier SQL fuera de lo permitido. |
| **Salida en formato JSON consistente** | El nodo de razonamiento estructura su respuesta final en JSON además del texto. | Facilita el consumo programático y el contraste con el endpoint `/metrics`. |

*(Las decisiones de Fase 2 y 3 se documentarán en detalle al implementarlas.)*

---

## Stack tecnológico

| Área | Tecnología |
|---|---|
| Base de datos | PostgreSQL 16 |
| Lenguaje | Python 3.13 |
| ORM | SQLAlchemy 2.x |
| API | FastAPI (pendiente, Fase 3) |
| Agente de IA | LangGraph + LangChain (pendiente, Fase 2) |
| LLM | Ollama (local, `qwen2.5:7b`) — sin API key |
| Gobernanza / calidad | Pydantic |
| Infraestructura | Docker / Docker Compose |

> **Decisión — LLM local con Ollama:** se eligió Ollama para no depender de una
> API key externa ni de costos por token. El agente corre 100% local. El modelo
> `qwen2.5:7b` ofrece buen desempeño en razonamiento y generación de SQL.

---

## Arquitectura de datos (Fase 1)

El modelo sigue un enfoque de *data warehouse* con dimensiones, hechos y KPIs.

### Tablas de dimensión (con historial SCD tipo 2)

- **`dim_channel`** — canales de marketing y su tarifa (CPC) base.
- **`dim_customer`** — clientes y sus atributos (segmento, ciudad).

Ambas implementan **Slowly Changing Dimension tipo 2**: cuando un atributo
cambia (por ejemplo, el CPC de un canal sube el mes siguiente), **no se
sobrescribe** el valor anterior. Se cierra la versión vigente
(`valid_to`, `is_current = false`) y se inserta una fila nueva vigente. Así el
histórico queda intacto y los KPIs de periodos pasados se pueden recalcular con
los valores que aplicaban entonces.

Columnas SCD2: `valid_from`, `valid_to` (NULL = vigente), `is_current`.

### Tabla de hechos

- **`fact_campaign_performance`** — desempeño consolidado por día y canal
  (ventas, inversión, transacciones, clientes nuevos, clics).

El **grano** es una fila por `(event_date, channel_id)`, forzado por una
restricción `UNIQUE`. Esta restricción es además la base de la deduplicación.

### Vista de KPIs

- **`vw_campaign_kpis`** — calcula automáticamente, desde la tabla de hechos:
  - **ROI** = (ventas − inversión) / inversión
  - **CAC** = inversión / clientes nuevos
  - **Tasa de conversión** = transacciones / clics

Se usa `NULLIF(x, 0)` en cada división para evitar errores por división entre
cero (devuelve NULL en lugar de romper).

---

## Gobernanza de datos (Fase 1)

Implementada con **Pydantic** en `validator.py` y orquestada en `pipeline.py`.
Cubre los tres requisitos de calidad exigidos:

1. **Unificación de fuentes.** Los nombres de canal se homologan a un valor
   canónico mediante un mapa de equivalencias. Ejemplos:
   `"FB Ads"`, `"facebook_ads"`, `"Facebook"`, `"FB"` → **`FACEBOOK`**.
   Los canales no mapeados se estandarizan (mayúsculas, guiones bajos) en lugar
   de descartarse, para no perder el dato.

2. **Consistencia.** Se rechazan registros con **montos negativos** o
   **fechas futuras**. La validación es por registro: un dato inválido se
   descarta y se contabiliza, sin abortar el lote completo.

3. **Deduplicación.** Al cargar la tabla de hechos se usa un **UPSERT**
   (`ON CONFLICT ... DO UPDATE`) contra la restricción `UNIQUE(event_date,
   channel_id)`. Si la combinación ya existe, se acumula sobre la fila existente
   en vez de insertar un duplicado.

La ingesta devuelve un resumen (`received`, `valid`, `rejected`,
`fact_rows_affected`) apto para exponerse por API.

---

## Seguridad del acceso de IA

Se aplica **defensa en profundidad** para el consumo de datos por parte del
agente:

1. **Usuario PostgreSQL de solo lectura (`insight_ro`).** El agente se conecta
   con un rol que únicamente tiene permiso `SELECT`. Cualquier intento de
   `INSERT`/`UPDATE`/`DELETE` es rechazado por el motor de base de datos.
2. **Lista blanca de tablas y columnas** (capa de aplicación, Fase 2). El
   ejecutor de SQL validará las consultas contra un conjunto permitido antes de
   ejecutarlas, mitigando SQL injection y alucinaciones del modelo.

---

## Estructura del repositorio

```
insight-extractor/
├── src/
│   ├── data/                  # Ingesta, validación (gobernanza) y ORM
│   │   ├── models.py          # Modelos SQLAlchemy (tablas, históricos, vista KPIs)
│   │   ├── pipeline.py        # Lógica de ingesta, unificación y limpieza
│   │   └── validator.py       # Validaciones de calidad (Pydantic / reglas)
│   ├── ai/                    # Inteligencia Artificial (LangGraph) — Fase 2
│   │   ├── agent.py           # Definición del grafo, estados y nodos
│   │   └── tools.py           # Herramientas de consulta SQL/KPIs del agente
│   └── main.py                # Servidor FastAPI con endpoints — Fase 3
├── migrations/                # Migraciones de BD (Alembic) — Fase 3
├── tests/
├── Dockerfile                 # Fase 3
├── docker-compose.yml         # Levanta PostgreSQL (y la app en Fase 3)
├── requirements.txt
├── .env.example               # Plantilla de variables de entorno
├── README.md
└── ARCHITECTURE.md            # Escalabilidad GCP y CI/CD Azure DevOps — Fase 3
```

---

## Puesta en marcha local

### Requisitos previos

- Docker Desktop
- Python 3.11+
- Ollama con el modelo descargado: `ollama pull qwen2.5:7b`

### Pasos

1. **Clonar y configurar variables de entorno:**
   ```bash
   cp .env.example .env
   ```

2. **Crear el entorno virtual e instalar dependencias:**
   ```bash
   python -m venv venv
   # Windows:  .\venv\Scripts\Activate.ps1
   # Linux/Mac: source venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Levantar PostgreSQL:**
   ```bash
   docker compose up -d
   ```

4. **Crear las tablas y la vista de KPIs:**
   ```bash
   python -c "from dotenv import load_dotenv; load_dotenv(); import sys; sys.path.insert(0,'src/data'); import models; models.init_db()"
   ```

5. **(Una vez) Crear el usuario de solo lectura para el agente:**
   ```bash
   docker exec -it insight_db psql -U insight -d insight_db -c "CREATE USER insight_ro WITH PASSWORD 'readonly_pass';"
   docker exec -it insight_db psql -U insight -d insight_db -c "GRANT CONNECT ON DATABASE insight_db TO insight_ro;"
   docker exec -it insight_db psql -U insight -d insight_db -c "GRANT USAGE ON SCHEMA public TO insight_ro;"
   docker exec -it insight_db psql -U insight -d insight_db -c "GRANT SELECT ON ALL TABLES IN SCHEMA public TO insight_ro;"
   docker exec -it insight_db psql -U insight -d insight_db -c "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO insight_ro;"
   ```

---

## Variables de entorno

| Variable | Descripción |
|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | Credenciales de la BD (rol administrador para migraciones e ingesta) |
| `POSTGRES_HOST` / `POSTGRES_PORT` | Host y puerto de la BD (`localhost` en local, `db` dentro de Docker) |
| `POSTGRES_RO_USER` / `POSTGRES_RO_PASSWORD` | Rol de solo lectura usado por el agente de IA |
| `OLLAMA_MODEL` | Modelo de Ollama (`qwen2.5:7b`) |
| `OLLAMA_BASE_URL` | URL del servidor Ollama (`http://localhost:11434`) |

---

## Estado por fases

- [x] **Fase 0 — Entorno:** Python, Git, Docker, Ollama configurados.
- [x] **Fase 1 — Ingesta, modelado y gobernanza:** dimensiones SCD2, tabla de
      hechos, vista de KPIs, validación (unificación, consistencia,
      deduplicación) e ingesta funcionando contra PostgreSQL.
- [x] **Seguridad — Usuario read-only** de PostgreSQL creado y verificado.
- [ ] **Fase 2 — Agente de IA (LangGraph):** nodos de intención, Text-to-SQL
      seguro y razonamiento/recomendación.
- [ ] **Fase 3 — API y DevOps:** endpoints FastAPI (`/chat`, `/data/ingest`,
      `/metrics`), Dockerfile, docker-compose completo y `ARCHITECTURE.md`.
