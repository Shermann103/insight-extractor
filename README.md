# Insight-Extractor & History Tracker

Sistema de ingeniería de datos e inteligencia artificial que **extrae** datos de
campañas de marketing y ventas desde varias fuentes, **garantiza su calidad**,
los **modela** en una base de datos con históricos y KPIs, y expone un **agente
de IA** conversacional a través de una API lista para producción.

En una frase: entra información desordenada de varias fuentes; sale información
confiable, indicadores de negocio y un asistente que los interpreta.

---

## Stack tecnológico

| Área | Tecnología | Rol en el proyecto |
|---|---|---|
| **Base de datos** | PostgreSQL 16 | Almacena dimensiones, hechos e históricos. |
| **Lenguaje** | Python 3.12 | Lenguaje base de todo el sistema. |
| **API** | FastAPI | Expone los endpoints de forma asíncrona. |
| **ORM** | SQLAlchemy | Mapea las tablas de la base de datos a objetos Python. |
| **Framework de IA** | LangGraph (sobre LangChain) | Orquesta el agente como un grafo de estados. |
| **LLM** | Ollama (`qwen2.5:7b`), local | Modelo de lenguaje sin API key ni costo por token. |
| **Gobernanza / calidad** | Pydantic | Valida la calidad de los datos en la ingesta. |
| **Migraciones** | Alembic | Versiona los cambios del esquema de la base de datos. |
| **Infraestructura** | Docker / Docker Compose | Ejecución local reproducible; base para el despliegue. |
| **Despliegue (teórico)** | GCP + Azure DevOps | Arquitectura de escalado y CI/CD (ver `ARCHITECTURE.md`). |

El sistema está **dockerizado** para ejecución local con un solo comando, con una
arquitectura pensada para escalar a **GCP** y un control de cambios bajo
estándares de **Git / Azure DevOps**.

---

# Fase 1 — Ingesta, modelado de datos y gobernanza

## 1. Las fuentes de datos

El sistema unifica **dos fuentes** independientes de información:

### Fuente A — Ventas (transaccional)

Representa los datos de ventas diarias que provendrían de un sistema
transaccional (como el sistema de caja de una tienda). Cada registro trae: ID de
transacción, cliente, monto, fecha y canal de venta. Se ingesta con la función
`ingest_sales()`.

### Fuente B — Inversión de marketing

Representa cuánto se gastó en publicidad, con impresiones y clics, por día y
canal. El enunciado pide simular "ingesta web/API y archivos estructurados", por
lo que la Fuente B se implementó en **dos formatos**:

- **Archivo estructurado (CSV):** `data_sources/marketing_spend.csv`, leído con
  `ingest_spend_from_csv()`.
- **API simulada (JSON):** `data_sources/marketing_spend_api.json`, leído con
  `ingest_spend_from_api()`.

Ambas fuentes son necesarias porque los KPIs combinan las dos: las ventas vienen
de A y la inversión de B.

## 2. La capa de validación (gobernanza)

Toda la información, sin importar la fuente, pasa por un **filtro de calidad**
antes de entrar a la base de datos. Está implementado con **Pydantic** en
`validator.py` y garantiza tres cosas:

### Unificación (normalización de canales)

El mismo canal llega escrito de muchas formas distintas. Un diccionario de
equivalencias (`CHANNEL_MAP`) normaliza el texto (minúsculas, sin espacios
sobrantes) y lo homologa a un valor estándar:

| Cómo llega | Se guarda como |
|---|---|
| FB Ads, facebook_ads, Facebook, fb | **FACEBOOK** |
| google ads, AdWords, google_ads | **GOOGLE** |
| ig, instagram_ads | **INSTAGRAM** |
| tik tok, tiktok_ads | **TIKTOK** |

Si llega un canal que no está en el diccionario, **no se descarta**: se
estandariza igual (mayúsculas, guiones bajos) para no perder el dato.

### Consistencia (validaciones nativas con Pydantic)

Los validadores de Pydantic (`field_validator` y `model_validator`) rechazan
automáticamente los datos incoherentes:

- **Montos negativos** en ventas o inversión (`amount < 0`, `spend < 0`).
- **Fechas futuras** (`event_date > hoy`).
- Clics o impresiones negativos.

La validación es **por registro**: si uno viene mal, se descarta y se cuenta,
pero no se cae todo el lote. Cada ingesta devuelve un resumen
`{received, valid, rejected}`.

### Duplicados

Se evitan a **nivel de base de datos**, que es el lugar más robusto:

- En `fact_campaign_performance`, una restricción `UNIQUE(event_date, channel_id)`
  impide dos filas para el mismo día y canal. La carga usa un **UPSERT**
  (`INSERT ... ON CONFLICT DO UPDATE`): si la combinación ya existe, actualiza la
  fila en lugar de duplicarla.
- En `dim_channel`, la función `ensure_channel_exists()` comprueba si el canal ya
  existe antes de crearlo, evitando canales repetidos.

## 3. Modelado del histórico y KPIs

Todo el esquema se define con **SQLAlchemy** en `src/data/models.py`. Consta de
tres tablas y una vista:

```
   dim_channel (catálogo canales)         dim_customer (catálogo clientes)
   ┌───────────────────────────┐          ┌───────────────────────────┐
   │ nombre del canal          │          │ código del cliente        │
   │ costo por clic (CPC)      │          │ segmento, ciudad          │
   │ desde / hasta / vigente   │          │ desde / hasta / vigente   │
   └─────────────┬─────────────┘          └───────────────────────────┘
                 │
                 │  un canal aparece en muchos días
                 │
                 ▼
   ┌─────────────────────────────────────────────────────────┐
   │ fact_campaign_performance (tabla principal de datos)    │
   │                                                         │
   │ fecha + canal  ← identifican cada fila (sin duplicados) │
   │ ventas totales        ← viene de la Fuente A            │
   │ inversión (gasto)     ← viene de la Fuente B            │
   │ nº transacciones      ← Fuente A                        │
   │ nº clientes nuevos    ← Fuente A                        │
   │ nº de clics           ← Fuente A + Fuente B (se suman)  │
   └─────────────────────────────┬───────────────────────────┘
                                 │
                                 ▼
   ┌─────────────────────────────────────────────────────────┐
   │ vw_campaign_kpis (vista que calcula los indicadores)    │
   │ ROI · CAC · Tasa de conversión                          │
   └─────────────────────────────────────────────────────────┘
```

### Tabla de hechos: `fact_campaign_performance`

Es la tabla central. Cada fila representa el desempeño de **un canal en un día**
(su "grano"). Unifica las dos fuentes sobre la misma fila `(event_date,
channel_id)`:

- La **Fuente A** llena las columnas de ventas: `total_sales_amount`,
  `num_transactions`, `num_new_customers`.
- La **Fuente B** llena las columnas de inversión: `marketing_spend`,
  `num_clicks` (impresiones).

Cada fuente escribe solo sus propias columnas; por eso son independientes y el
orden de ingesta no altera el resultado. El único campo que ambas comparten es
`num_clicks`, que se **acumula**. Así, una fila queda completa con datos de
ambas fuentes, lista para calcular los KPIs.

### Histórico SCD tipo 2: `dim_channel`

Esta tabla guarda el histórico de las tarifas de canal (costo por clic) **sin
sobrescribir el pasado**. Cuando la tarifa de un canal cambia, en lugar de
reemplazar el valor anterior, se **cierra** la versión vigente y se **crea** una
nueva. Cada versión lleva tres marcas:

- `valid_from` — desde cuándo aplica.
- `valid_to` — hasta cuándo aplicó (`NULL` = versión vigente).
- `is_current` — bandera de la versión actual.

Ejemplo — el costo por clic de Facebook sube en febrero:

| channel_code | base_cpc | valid_from | valid_to | is_current |
|---|---|---|---|---|
| FACEBOOK | 0.50 | 2026-01-01 | 2026-01-31 | false |
| FACEBOOK | 0.70 | 2026-02-01 | *(null)* | true |

De este modo, el ROI de enero se puede recalcular con la tarifa que aplicaba en
enero. (El mismo patrón está disponible en `dim_customer` para el histórico de
clientes.)

### KPIs calculados: la vista `vw_campaign_kpis`

Los indicadores se calculan con una **vista** (una consulta SQL guardada), no una
tabla. La ventaja: los KPIs siempre reflejan los datos más recientes sin
duplicar información. La vista toma cada fila de la tabla de hechos, la une con
`dim_channel` para traer el nombre del canal, y añade tres columnas calculadas:

| KPI | Fórmula |
|---|---|
| **ROI** | (ventas − inversión) / inversión |
| **CAC** | inversión / clientes nuevos |
| **Conversión** | transacciones / clics |

Cada división usa `NULLIF(divisor, 0)`: si el divisor es cero (p. ej., un canal
sin clientes nuevos), devuelve `NULL` en lugar de dar un error.

---

# Fase 2 — El agente de IA (LangGraph)

## Diseño del agente

El agente se construyó con **LangGraph**, que lo organiza como un **grafo de
estados**: un diagrama de flujo que se ejecuta de verdad, donde la información
pasa por nodos conectados. Un "expediente" compartido (el estado) viaja de nodo
en nodo, y cada uno le agrega su resultado.

### Herramientas del agente y cómo accede a ellas

La herramienta del agente es la función que consulta la base de datos de forma
segura (`run_readonly_sql`, en `src/ai/tools.py`). A diferencia del enfoque donde
el modelo "decide solo" cuándo usar herramientas, aquí **el código las invoca en
el momento correcto del flujo**. Se eligió así porque los modelos pequeños (7B)
suelen equivocarse al decidir cuándo llamar herramientas; controlarlo en el
código hace el flujo predecible y seguro.

### El grafo de estados y sus nodos

```
   Pregunta del usuario
            │
            ▼
   ┌──────────────────┐
   │ 1. Intención     │  ¿datos, análisis o actualización?
   └────────┬─────────┘
            │
            │  (Bifurcación según la intención)
            │
      ┌─────┴─────────────────┐
      │                       │
      │                       ▼
      │               (datos / análisis)
      │                       │
  (actualización)       ┌─────────────┐
      │                 │ 2. Text-to- │   Traduce a SQL 
      │                 │    SQL      │  y ejecuta seguro
      │                 └──────┬──────┘
      │                        │
      └───────────┬────────────┘
                  ▼
          ┌──────────────┐
          │ 3. Razonar   │  genera diagnóstico + JSON
          └──────┬───────┘
                 ▼
             Respuesta
```

**Nodo 1 — Clasificación de intención.** Le pregunta al modelo si la consulta del
usuario es de tipo `data` (datos duros), `analysis` (análisis estratégico de
KPIs) o `update` (tarea de actualización). Como el modelo a veces responde de
más, el código no confía ciegamente: busca cuál de las tres palabras aparece en
la respuesta. Según el resultado, una bifurcación decide el camino: `update` va
directo a la respuesta (el agente es de solo lectura); `data` y `analysis` pasan
al nodo de SQL.

**Nodo 2 — Text-to-SQL controlado.** Traduce la pregunta en lenguaje natural a
una consulta SQL. Para evitar que el modelo "alucine" (invente tablas o
columnas), la instrucción incluye tres ingredientes:

1. El rol y la orden clara ("genera una consulta SELECT, responde solo con SQL").
2. El **esquema** de la base (las tablas y columnas reales disponibles).
3. Ejemplos resueltos — la técnica de **Few-Shot prompting**: se le muestran
   varios pares de *pregunta → SQL correcto* antes de su tarea, y el modelo imita
   el patrón. Es enseñar con ejemplos en lugar de con reglas abstractas.

El SQL generado nunca se ejecuta a ciegas: pasa primero por las validaciones de
seguridad (ver más abajo). Si el modelo genera algo no permitido, se rechaza y se
reporta el error de forma controlada.

**Nodo 3 — Razonamiento y recomendación.** Toma los datos que devolvió la
consulta, analiza los KPIs y genera un **diagnóstico de negocio** con
recomendaciones de optimización (por ejemplo, en qué canal invertir más y en cuál
menos). Antes de razonar, detecta los KPIs en `NULL` y los interpreta como datos
faltantes, no como resultados de cero, para no generar falsas alarmas.

## Políticas de gobernanza en el consumo de IA

### Cómo se evitan SQL injection y mutaciones accidentales

Se aplica **defensa en profundidad**, con dos capas independientes:

1. **Usuario de base de datos de solo lectura (`insight_ro`).** El agente se
   conecta con un rol que solo tiene permiso `SELECT`. Cualquier intento de
   `INSERT`, `UPDATE` o `DELETE` es rechazado por el propio motor de PostgreSQL.
2. **Lista blanca en la aplicación (`tools.py`).** Antes de ejecutar, cada
   consulta se valida: debe ser una única sentencia `SELECT`/`WITH`, sin palabras
   de escritura, y solo puede referenciar tablas permitidas.

Si una capa fallara, la otra sigue protegiendo. El candado a nivel de motor es
infranqueable desde la aplicación.

### Formato de la salida

El agente estructura su respuesta en **dos formatos a la vez**:

- Un **texto** en lenguaje natural (el diagnóstico, para una persona).
- Un **JSON estructurado**, para consumo programático, con esta forma:

```json
{
  "status": "ok",
  "intent": "data",
  "sql": "SELECT channel_code, roi FROM vw_campaign_kpis ...",
  "data": { "columns": [...], "rows": [...], "row_count": 4 },
  "missing_inputs": [],
  "diagnosis": "Texto del diagnóstico de negocio..."
}
```

El campo `missing_inputs` lista los KPIs que no se pudieron calcular por falta de
datos, y `sql` deja trazado exactamente qué consulta se ejecutó.

---

# Fase 3 — API, exposición e integración

El sistema es accesible de forma programática mediante una **API con FastAPI**
(`src/main.py`), lista para producción. Expone tres endpoints.

## `POST /chat` — conversar con el agente

Interactúa con el agente de LangGraph de forma **asíncrona**. El endpoint está
declarado como `async`, pero el agente (que llama a Ollama) es una operación
bloqueante; para no congelar el servidor mientras el modelo piensa, la llamada se
ejecuta en un **hilo aparte** (`run_in_threadpool`). Así el servidor sigue
atendiendo otras peticiones mientras el agente trabaja. Recibe una pregunta y
devuelve el diagnóstico en texto + el JSON estructurado.

## `POST /data/ingest` — disparar la ingesta

Dispara todo el proceso de ingesta con gobernanza. En el cuerpo de la petición se
envía lo que se quiera cargar (ventas, tarifas de canal, inversión por API o
CSV), y el endpoint:

1. **Valida** cada registro con la capa de gobernanza (unificación de canales,
   rechazo de negativos y fechas futuras, descritos en la Fase 1).
2. **Consolida** los datos por día y canal.
3. **Actualiza los históricos:** las tarifas de canal se procesan con la lógica
   SCD tipo 2 (si el costo por clic cambió, se cierra la versión vigente y se abre
   una nueva, sin borrar el pasado); la tabla de hechos se actualiza con UPSERT
   (sin duplicar).

Devuelve un resumen por fuente con los conteos de recibidos, válidos y rechazados.

## `GET /metrics` — KPIs actuales

Devuelve, en formato JSON, los KPIs actuales (ROI, CAC, conversión) **calculados
directamente en la base de datos** (leyendo la vista `vw_campaign_kpis` con el
usuario de solo lectura). Sirve para **contrastar** los números reales de la base
con lo que responde el agente de IA.

## Arquitectura y DevOps

### El `docker-compose.yml`

Levanta todo el sistema con un solo comando (`docker compose up`). Define dos
servicios que arrancan en orden:

1. **`db`** — el contenedor de PostgreSQL. Tiene un *healthcheck* que avisa
   cuándo la base está realmente lista para aceptar conexiones.
2. **`api`** — el contenedor de la aplicación. Gracias a `depends_on: condition:
   service_healthy`, **espera a que la base esté sana** antes de arrancar. Al
   iniciar, su `entrypoint.sh` ejecuta tres pasos en secuencia:
   1. espera a PostgreSQL,
   2. ejecuta las **migraciones de Alembic** (crea las tablas, la vista de KPIs y
      el usuario de solo lectura),
   3. inicia el servidor **FastAPI** con uvicorn.

El agente sigue usando el Ollama que corre en la máquina anfitriona; el contenedor
lo alcanza mediante `host.docker.internal`.

### Escalado en la nube y CI/CD

El documento `ARCHITECTURE.md` describe cómo se escalaría la solución en **GCP**
(Cloud Run, Cloud SQL, BigQuery, dbt, Vertex AI) y cómo se estructuraría el
pipeline de **CI/CD en Azure DevOps**, con foco en el despliegue seguro de las
migraciones de base de datos.

---

# Descarga y puesta en marcha

## Requisitos previos

Verifica que tienes instalado:

- **Python 3.11+** — `python --version`
- **Git** — `git --version`
- **Docker Desktop** — `docker --version` (debe estar abierto y corriendo)
- **Ollama** — `ollama --version`

## Opción A — Todo con Docker (recomendada)

Es la forma más simple: un solo comando levanta la base, corre las migraciones e
inicia la API.

**1. Clonar el repositorio:**
```bash
git clone https://github.com/TU-USUARIO/insight-extractor.git
cd insight-extractor
```

**2. Configurar las variables de entorno:**
```bash
cp .env.example .env      # Windows: Copy-Item .env.example .env
```

**3. Descargar el modelo de IA:**
```bash
ollama pull qwen2.5:7b
```
> Si Ollama falla por un error de GPU/CUDA, fuérzalo a usar el procesador:
> en Windows ejecuta `setx OLLAMA_NUM_GPU 0`, reinicia Ollama y vuelve a intentar.

**4. Levantar todo el sistema:**
```bash
docker compose up --build
```
Espera a ver el mensaje `Application startup complete`. La API queda disponible en
`http://localhost:8000`.

**5. (Opcional) Cargar datos de demostración:**

En otra terminal, con un entorno de Python y las dependencias instaladas:
```bash
python seed_demo.py
```
Esto carga 4 canales con datos creíbles (Facebook rinde bien, TikTok pierde) para
que la demo sea presentable.

## Opción B — API en local (para desarrollo)

Útil si quieres modificar el código y ver los cambios al instante.

```bash
# 1. Entorno virtual e instalación de dependencias
python -m venv venv
.\venv\Scripts\Activate.ps1        # Windows
# source venv/bin/activate          # Linux/Mac
pip install -r requirements.txt

# 2. Levantar solo la base de datos con Docker
docker compose up -d db

# 3. Aplicar las migraciones
alembic upgrade head

# 4. Cargar datos de demostración
python seed_demo.py

# 5. Iniciar la API
cd src
uvicorn main:app --reload
```

## Cómo usar la API

Con la API corriendo, abre en el navegador:

```
http://localhost:8000/docs
```

Es la interfaz interactiva de FastAPI, donde puedes probar los tres endpoints con
botones. Orden sugerido:

1. **GET /metrics** — el más simple, no usa IA. Devuelve los KPIs actuales.
2. **POST /data/ingest** — para cargar datos. Ejemplo de cuerpo:
   ```json
   {
     "sales": [
       {"transaction_id": "t1", "customer_code": "c1", "amount": 300,
        "event_date": "2026-08-03", "channel": "FB Ads", "clicks": 12,
        "is_new_customer": true}
     ]
   }
   ```
3. **POST /chat** — para hablar con el agente (requiere Ollama corriendo). Ejemplo:
   ```json
   {"question": "¿En qué canal debería invertir más y en cuál menos?"}
   ```

---

## Estructura del repositorio

```
insight-extractor/
├── src/
│   ├── data/
│   │   ├── models.py          # Tablas SQLAlchemy, SCD2 y vista de KPIs
│   │   ├── pipeline.py        # Ingesta multi-fuente y unificación
│   │   └── validator.py       # Gobernanza / calidad con Pydantic
│   ├── ai/
│   │   ├── agent.py           # Grafo LangGraph (intención, SQL, razonamiento)
│   │   └── tools.py           # Ejecutor SQL seguro (read-only + lista blanca)
│   └── main.py                # API FastAPI con los tres endpoints
├── data_sources/
│   ├── marketing_spend.csv    # Fuente B (archivo estructurado)
│   └── marketing_spend_api.json # Fuente B (API simulada)
├── migrations/                # Migraciones de Alembic
│   ├── env.py
│   └── versions/
│       └── 0001_initial_schema.py
├── seed_demo.py               # Carga datos de demostración
├── docker-compose.yml         # Levanta BD + API
├── Dockerfile
├── entrypoint.sh              # Espera BD, migra y arranca la API
├── alembic.ini
├── requirements.txt
├── .env.example
├── README.md
└── ARCHITECTURE.md            # Escalado en GCP y CI/CD en Azure DevOps
```

## Variables de entorno

| Variable | Descripción |
|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | Credenciales de la base (usuario administrador). |
| `POSTGRES_HOST` / `POSTGRES_PORT` | Host y puerto (`localhost` en local, `db` dentro de Docker). |
| `POSTGRES_RO_USER` / `POSTGRES_RO_PASSWORD` | Usuario de solo lectura para el agente. |
| `OLLAMA_MODEL` | Modelo de Ollama (`qwen2.5:7b`). |
| `OLLAMA_BASE_URL` | URL del servidor Ollama. |
