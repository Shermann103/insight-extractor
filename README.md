# Insight-Extractor & History Tracker

Este proyecto es un sistema que **recoge datos de campañas de marketing y ventas,
los limpia y organiza en una base de datos, y luego permite hacerle preguntas en
lenguaje natural a un asistente de inteligencia artificial** que responde con
análisis de negocio.

En pocas palabras: entra información desordenada de varias fuentes, sale
información confiable y un asistente que la interpreta.

Este documento explica, **punto por punto siguiendo el enunciado de la prueba**,
qué se pedía, cómo se resolvió y qué decisiones se tomaron. Al final está la guía
para instalarlo y ejecutarlo.

---

## El objetivo general

La prueba pide diseñar un sistema que haga cuatro cosas:

1. **Extraer** datos de varias fuentes (una base de datos y archivos/APIs).
2. **Garantizar la calidad** del dato antes de guardarlo (que no haya errores,
   duplicados ni incoherencias).
3. **Guardar y modelar** la información en una base de datos robusta, con
   históricos y KPIs (indicadores de negocio).
4. **Activar un agente de IA** que consulte esos datos, razone sobre ellos y
   entregue reportes a través de una API web.

El trabajo se divide en tres fases. Vamos una por una.

---

# FASE 1 — Ingesta, Modelado de Datos y Gobernanza

Esta fase construye la base: de dónde vienen los datos, cómo se limpian y cómo se
guardan.

## Punto 1 — El origen de datos: dos fuentes

**Qué se pedía:** simular dos fuentes de información sobre campañas de marketing y
ventas. El enunciado detalla una (Fuente A) y deja la otra abierta.

**Cómo se resolvió:**

- **Fuente A — Ventas (transaccional).** Son los datos de ventas diarias: número
  de transacción, cliente, monto, fecha y canal por el que se vendió. Es la
  fuente que el enunciado define explícitamente.

- **Fuente B — Inversión en publicidad.** Cuánto se gastó en anuncios, cuántas
  veces se mostraron (impresiones) y cuántos clics generaron, por día y canal.

**Decisión que se tomó:** el enunciado no dice cuál es la segunda fuente, pero sí
pide (más adelante) que la tabla final "unifique **ventas e inversión**". Las
ventas ya venían de la Fuente A, así que dedujimos que la Fuente B debía ser la
**inversión publicitaria**. Además, como el enunciado menciona "ingesta web/API y
archivos estructurados", la Fuente B se hizo en **dos formatos** a la vez:

- un archivo **CSV** (representa un archivo estructurado que alguien exporta),
- un archivo **JSON** (representa la respuesta de una API web).

Así quedan cubiertas ambas formas de ingesta que menciona la prueba.

## Punto 2 — Gobernanza de datos (que el dato sea confiable)

**Qué se pedía:** una capa de validación que garantice tres cosas antes de
guardar cualquier dato.

**Cómo se resolvió:** se creó un "filtro de calidad" (usando la herramienta
Pydantic) por el que pasa **todo** dato de ambas fuentes antes de entrar a la base
de datos. Ese filtro revisa tres cosas:

### a) Unificar los nombres de los canales

El problema: el mismo canal llega escrito de mil formas. "FB Ads", "facebook_ads"
y "Facebook" son todos el mismo canal, pero un computador los ve como distintos.

La solución: una "tabla de traducción" que convierte todas las variantes a un
nombre único y estándar.

| Cómo llega escrito | Cómo se guarda |
|---|---|
| FB Ads, facebook_ads, Facebook, fb | **FACEBOOK** |
| google ads, AdWords, google_ads | **GOOGLE** |
| ig, instagram_ads | **INSTAGRAM** |
| tik tok, tiktok_ads | **TIKTOK** |

**Decisión:** si llega un canal que no está en la tabla de traducción, **no se
descarta**; se guarda igual pero estandarizado (en mayúsculas). Así no se pierde
información aunque aparezca un canal nuevo.

### b) Coherencia: rechazar datos imposibles

Se rechazan automáticamente:

- Ventas o gastos con **montos negativos** (no existe vender −50 pesos).
- Registros con **fecha futura** (no se pueden tener ventas de mañana).

**Decisión:** si un registro viene mal, **se descarta solo ese** y se sigue con
los demás. Un dato malo no arruina toda la carga. Al final, el sistema informa
cuántos registros entraron y cuántos se rechazaron.

### c) Evitar duplicados

El problema: si por error se carga el mismo dato dos veces, no debe duplicarse en
la base.

La solución: la base de datos tiene una **regla de unicidad** por día y canal. Si
llega un dato para un día y canal que ya existen, en vez de crear una fila
repetida, **actualiza la que ya está**.

**Decisión:** esta regla se puso **en la base de datos misma**, no solo en el
código. Es más seguro: aunque el programa fallara, la base nunca aceptaría un
duplicado.

## Punto 3 — Modelado del histórico y KPIs

**Qué se pedía:** diseñar las tablas con SQLAlchemy, con una tabla de hechos, un
histórico de cambios (SCD) y una vista de KPIs.

Antes de los detalles, así se ven las tablas y cómo se conectan:

```
   dim_channel (catálogo de canales)      dim_customer (catálogo de clientes)
   ┌───────────────────────────┐          ┌───────────────────────────┐
   │ nombre del canal          │          │ código del cliente        │
   │ costo por clic (CPC)      │          │ segmento, ciudad          │
   │ desde / hasta / vigente   │          │ desde / hasta / vigente   │
   └─────────────┬─────────────┘          └───────────────────────────┘
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

### ¿Qué es cada tabla y para qué sirve?

Hay tres tipos de tablas, con roles distintos:

- **Tablas de catálogo (dimensiones):** describen las "cosas" del negocio.
  - `dim_channel` — el catálogo de canales de marketing y su costo por clic.
  - `dim_customer` — el catálogo de clientes y sus datos (segmento, ciudad).

- **Tabla principal (de hechos):** `fact_campaign_performance` guarda los números
  medibles (ventas, inversión, clics...) de cada día y canal. Es el corazón del
  sistema.

- **Vista de indicadores:** `vw_campaign_kpis` no guarda datos, los **calcula**
  automáticamente a partir de la tabla principal.

### ¿Cómo se relacionan?

La tabla principal está conectada al catálogo de canales: cada fila de datos
"apunta" a un canal del catálogo (esto se llama *clave foránea*). Un canal puede
tener muchos días de datos, pero cada dato pertenece a un solo canal.

### ¿Cómo se llenan?

| Tabla | Cómo se llena | Con datos de |
|---|---|---|
| `dim_channel` | Al cargar tarifas o cuando aparece un canal nuevo | Tarifas de canal |
| `dim_customer` | Al cargar clientes | Datos de clientes |
| `fact_campaign_performance` | Con las funciones de ingesta de ventas e inversión | Fuentes A y B |
| `vw_campaign_kpis` | No se llena: se calcula sola | Deriva de la tabla principal |

**Cómo se combinan las dos fuentes:** las ventas (Fuente A) y la inversión
(Fuente B) escriben sobre la **misma fila** (identificada por día + canal). Cada
fuente llena sus propias columnas: A pone las ventas, B pone el gasto. Son
**independientes** (no dependen una de la otra) y **da igual cuál se cargue
primero**: el resultado final es el mismo. Lo único que ambas comparten son los
clics, que se **suman** entre las dos.

> **Nota honesta:** como los clics y el gasto se suman, la carga de inversión
> está pensada para hacerse **una vez por lote de datos**. Si se corriera dos
> veces sobre lo mismo, sumaría de nuevo. En un sistema de producción esto se
> controlaría marcando los lotes ya procesados.

### El histórico de cambios (SCD tipo 2)

**Qué se pedía:** guardar el histórico de cambios sin borrar el pasado. Ejemplo
del enunciado: si el costo por clic de un canal cambia el mes siguiente, no se
debe sobrescribir el valor anterior.

**Cómo se resolvió:** en vez de reemplazar el dato viejo, se **cierra** y se crea
uno nuevo. Cada versión lleva tres marcas: desde cuándo aplica, hasta cuándo
aplicó, y si es la versión vigente.

Ejemplo — el costo por clic de Facebook sube en febrero:

| Canal | Costo por clic | Desde | Hasta | ¿Vigente? |
|---|---|---|---|---|
| FACEBOOK | 0.50 | 1 ene | 31 ene | No |
| FACEBOOK | 0.70 | 1 feb | *(sigue)* | Sí |

Así, el ROI de enero se puede recalcular con el costo que aplicaba **en enero**,
sin que el cambio de febrero lo distorsione.

**Decisión:** el enunciado pedía hacerlo sobre clientes **o** canales; se hizo
sobre **ambos** para demostrar mejor el manejo del patrón.

### Los KPIs (indicadores de negocio)

**Qué se pedía:** una vista que calcule automáticamente ROI, CAC y tasa de
conversión.

**Cómo se resolvió:** la vista `vw_campaign_kpis` los calcula así:

| Indicador | Qué mide | Cómo se calcula |
|---|---|---|
| **ROI** | Retorno de la inversión | (ventas − inversión) / inversión |
| **CAC** | Costo de conseguir un cliente | inversión / clientes nuevos |
| **Conversión** | % de clics que se vuelven venta | transacciones / clics |

**Decisión:** se usó una **vista** (una consulta guardada que se calcula al
momento) en lugar de una tabla, para que los indicadores siempre reflejen los
datos más recientes sin tener que recalcularlos a mano. Además, si algún divisor
es cero (por ejemplo, un canal sin clientes nuevos), el sistema devuelve "sin
dato" en lugar de dar un error.

---

# FASE 2 — El Agente de Inteligencia Artificial

Una vez los datos están limpios y guardados, esta fase construye el asistente que
los interpreta.

**Qué se pedía:** un agente conversacional (con LangGraph) que reciba preguntas
en lenguaje natural y tenga varios "pasos de pensamiento".

**Cómo se resolvió:** el agente funciona como una línea de montaje con tres
estaciones (nodos), tal como pide el enunciado:

1. **Estación 1 — Entender la intención.** Lee la pregunta y decide qué quiere el
   usuario: ¿datos concretos?, ¿un análisis estratégico?, ¿actualizar algo?

2. **Estación 2 — Traducir a SQL.** Convierte la pregunta en lenguaje humano
   ("¿cuál es el ROI por canal?") en una consulta técnica a la base de datos.

3. **Estación 3 — Razonar y recomendar.** Toma los datos que obtuvo y redacta un
   diagnóstico de negocio con recomendaciones.

A continuación se explica **cómo funciona cada mecanismo por dentro** (todo el
código está en `src/ai/agent.py`).

## Cómo se construyó el agente con LangGraph

LangGraph organiza al agente como un **diagrama de flujo que se ejecuta de
verdad**: cajas (nodos) conectadas por flechas (por dónde va la información).

La pieza central es el **"expediente" compartido** (en el código, `AgentState`):
un paquete de información que viaja de estación en estación. Empieza conteniendo
solo la pregunta del usuario, y cada estación le va agregando su resultado
(primero la intención, luego el SQL, luego los datos, al final la respuesta).

```
   Pregunta del usuario
          │
          ▼
   ┌──────────────────┐
   │ 1. Intención     │  ¿datos, análisis o actualización?
   └────────┬─────────┘
            │  (flecha que se bifurca según la intención)
      ┌─────┴─────────────────┐
      ▼                       ▼
  (actualización)      (datos / análisis)
      │                       │
      │                 ┌─────────────┐
      │                 │ 2. Text-to- │  traduce a SQL y lo ejecuta seguro
      │                 │    SQL      │
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

> **Detalle técnico — el grafo.** Se define con `StateGraph(AgentState)`. Se
> registran los nodos con `add_node()`, se conectan con `add_edge()`, y la
> bifurcación de la estación 1 se hace con `add_conditional_edges()`. Al final
> `compile()` lo vuelve ejecutable. El grafo se invoca con `agent_app.invoke(...)`.

### Sobre las "herramientas" (tools) del agente

El enunciado pide un agente "con acceso a herramientas". Su herramienta es la
función que consulta la base de datos de forma segura (`run_readonly_sql`, en
`src/ai/tools.py`).

> **Decisión de diseño (importante).** Hay dos formas de darle herramientas a un
> agente: (a) dejar que el **modelo decida solo** cuándo usarlas, o (b) que el
> **código las invoque** en el momento correcto del flujo. Elegimos la opción (b):
> el nodo de SQL llama a la herramienta directamente. ¿Por qué? Los modelos
> pequeños (como el nuestro, de 7B) suelen equivocarse al decidir cuándo y cómo
> llamar herramientas. Al controlarlo nosotros en el código, el flujo es
> **predecible y seguro**. Ganamos control y seguridad a cambio de menos
> autonomía del modelo — un intercambio deseable para un sistema que toca una
> base de datos.

## Estación 1: cómo se determina la intención

Se le envía al modelo una instrucción corta: *"clasifica esta pregunta en UNA
palabra: `data` (datos duros), `analysis` (análisis de KPIs) o `update`
(actualización)"*, junto con la pregunta del usuario. Estas son exactamente las
tres categorías que pide el enunciado.

Como los modelos a veces responden de más ("La intención es data porque..."), **no
se confía ciegamente** en la respuesta: el código busca cuál de las tres palabras
aparece y esa es la intención elegida. Si no aparece ninguna, se usa `analysis`
por defecto (la opción más segura, porque desemboca igual en una consulta de
datos).

Luego, una **flecha que se bifurca** usa esa intención: si es `update`, va directo
a la respuesta (actualizar datos no es tarea de un agente de solo lectura; eso lo
hará el endpoint `/data/ingest` en la Fase 3); si es `data` o `analysis`, pasa a
la estación de SQL.

## Estación 2: cómo se traduce lenguaje natural a SQL (Text-to-SQL)

Esta es la parte más delicada, porque un modelo puede "alucinar" (inventar tablas
o columnas que no existen). Para evitarlo, la instrucción que se le da tiene **tres
ingredientes**:

1. **El rol y la orden clara:** "eres experto en PostgreSQL, genera UNA sola
   consulta de solo lectura (SELECT), responde solo con el SQL".

2. **El mapa de la base de datos:** se le muestran las tablas y columnas que
   existen realmente. Sin esto, inventaría nombres. Con esto, se limita a lo real.

3. **Ejemplos resueltos (la técnica *Few-Shot*):** esto es lo que pide
   explícitamente el enunciado. "Few-shot" significa **enseñar con ejemplos** en
   lugar de con reglas. Se le muestran varios pares de *pregunta → SQL correcto*
   antes de su tarea, y el modelo **imita el patrón**. Es como mostrarle a alguien
   tres ejercicios resueltos antes de pedirle que resuelva el cuarto.

Ejemplos que se le dan al modelo (few-shot):

| Pregunta de ejemplo | SQL que se le muestra como correcto |
|---|---|
| ¿Cuál es el ROI por canal? | `SELECT channel_code, roi FROM vw_campaign_kpis;` |
| ¿Qué canal tuvo más ventas? | `SELECT channel_code, SUM(total_sales_amount) ... GROUP BY ...` |
| Tarifas vigentes de canales | `SELECT channel_code, base_cpc FROM dim_channel WHERE is_current = true;` |

Después, la respuesta del modelo se **limpia** (se le quitan explicaciones o
adornos de formato y se toma solo la consulta).

> **Seguridad — el SQL no se ejecuta a ciegas.** La consulta generada pasa
> primero por los dos candados (lista blanca + usuario de solo lectura, descritos
> abajo). Si el modelo generó algo no permitido, **se rechaza y se reporta el
> error de forma controlada**, en vez de ejecutarlo. Así, aunque el modelo se
> equivoque, la base de datos queda protegida.

## Estación 3: cómo se generan los reportes y dónde queda el JSON

La última estación toma los datos que trajo la consulta y produce **dos salidas a
la vez**:

- **Un texto** en lenguaje natural: el diagnóstico de negocio con recomendaciones,
  escrito para que lo lea una persona. Se genera con una instrucción que le dice al
  modelo "actúa como analista de negocio y redacta un diagnóstico con estos datos".

- **Un JSON estructurado**: un paquete de datos ordenado, pensado para que otro
  programa lo consuma. Contiene: el estado, la intención detectada, el SQL que se
  usó, los datos crudos, la lista de datos faltantes (si los hay) y el diagnóstico.

> **¿Dónde queda ese JSON?** Por ahora, el agente lo **devuelve en memoria**: la
> función `run_agent()` lo retorna a quien la llame, y al probar por consola se
> imprime en pantalla. Todavía **no se guarda en disco ni se expone por web**. Eso
> es justo lo que hará la **Fase 3**: el endpoint `/chat` tomará este mismo JSON y
> lo entregará como respuesta de la API. En otras palabras, el agente ya *produce*
> el reporte estructurado; la Fase 3 solo lo *publicará*.

## Seguridad: que el agente solo pueda mirar, no tocar

**Qué se pedía:** que el agente sea de **solo lectura**, para evitar que borre o
modifique datos por error, y protegerlo de ataques (SQL injection).

**Cómo se resolvió:** con **dos candados independientes**:

1. **Un usuario de base de datos que solo puede leer.** Aunque el agente
   intentara borrar algo, la base de datos se lo impediría.

2. **Una lista blanca en el código.** Antes de ejecutar cualquier consulta, el
   sistema revisa que sea solo de lectura y que use únicamente las tablas
   permitidas. Cualquier cosa sospechosa se bloquea antes de llegar a la base.

> **Decisión:** dos candados en lugar de uno. Si un candado fallara, el otro
> sigue protegiendo. Es el principio de "defensa en profundidad".

## Respuesta en dos formatos

**Qué se pedía:** que el agente responda en JSON (formato para programas) además
del texto en lenguaje natural.

**Cómo se resolvió:** cada respuesta trae las dos cosas: el diagnóstico escrito
para que lo lea una persona, y un bloque JSON estructurado (con la consulta usada,
los datos y el análisis) para que lo consuma otro programa.

**Mejora extra:** el agente distingue entre "el resultado es cero" y "falta el
dato para calcularlo". Si un indicador no se puede calcular porque falta
información, lo dice claramente en lugar de inventar una alarma falsa.

---

# FASE 3 — API, Exposición e Integración

*(En construcción.)*

**Qué se pedirá:** exponer todo a través de una API web con tres puertas de
entrada (endpoints):

- `/chat` — para conversar con el agente de IA.
- `/data/ingest` — para disparar la carga y validación de datos.
- `/metrics` — para obtener los KPIs directamente de la base (y poder
  contrastarlos con lo que dice el agente).

Y además: un `docker-compose.yml` que levante todo junto (base de datos +
migraciones + API) y un documento `ARCHITECTURE.md` que explique cómo se escalaría
en la nube (GCP) y cómo sería el pipeline de despliegue (CI/CD en Azure DevOps).

---

# Puesta en marcha local

## Requisitos previos

Verifica que tienes instalado (o instálalo):

- **Python 3.11+** — comprueba con `python --version`
- **Git** — `git --version`
- **Docker Desktop** — `docker --version` (debe estar abierto y corriendo)
- **Ollama** — `ollama --version` (el motor que corre la IA en local)

## Paso 1 — Clonar el repositorio

```bash
git clone https://github.com/TU-USUARIO/insight-extractor.git
cd insight-extractor
```

## Paso 2 — Crear el entorno virtual e instalar dependencias

```bash
python -m venv venv
# Windows:
.\venv\Scripts\Activate.ps1
# Linux/Mac:
source venv/bin/activate

pip install -r requirements.txt
```

> En Windows, si al activar sale un error de permisos, ejecuta una vez:
> `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`

## Paso 3 — Configurar las variables de entorno

```bash
cp .env.example .env      # Windows: Copy-Item .env.example .env
```

## Paso 4 — Descargar el modelo de IA

```bash
ollama pull qwen2.5:7b
```

> Si Ollama falla al arrancar el modelo por un error de GPU/CUDA, fuérzalo a usar
> el procesador (CPU): en Windows ejecuta `setx CUDA_VISIBLE_DEVICES ""`, reinicia
> Ollama y vuelve a intentar. Es más lento pero estable.

## Paso 5 — Levantar la base de datos PostgreSQL

```bash
docker compose up -d
docker compose ps          # el contenedor "insight_db" debe verse "healthy"
```

## Paso 6 — Crear las tablas y la vista de KPIs

```bash
python -c "from dotenv import load_dotenv; load_dotenv(); import sys; sys.path.insert(0,'src/data'); import models; models.init_db()"
```

Comprobar que se crearon:

```bash
docker exec -it insight_db psql -U insight -d insight_db -c "\dt"   # tablas
docker exec -it insight_db psql -U insight -d insight_db -c "\dv"   # vista
```

## Paso 7 — Crear el usuario de solo lectura (una sola vez)

Es el usuario restringido que usará el agente de IA:

```bash
docker exec -it insight_db psql -U insight -d insight_db -c "CREATE USER insight_ro WITH PASSWORD 'readonly_pass';"
docker exec -it insight_db psql -U insight -d insight_db -c "GRANT CONNECT ON DATABASE insight_db TO insight_ro;"
docker exec -it insight_db psql -U insight -d insight_db -c "GRANT USAGE ON SCHEMA public TO insight_ro;"
docker exec -it insight_db psql -U insight -d insight_db -c "GRANT SELECT ON ALL TABLES IN SCHEMA public TO insight_ro;"
docker exec -it insight_db psql -U insight -d insight_db -c "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO insight_ro;"
```

## Paso 8 — Cargar datos de ejemplo

```bash
python test_fase1_completa.py
```

Ver los KPIs calculados:

```bash
docker exec -it insight_db psql -U insight -d insight_db -P pager=off -c "SELECT channel_code, total_sales_amount, marketing_spend, roi, cac, conversion_rate FROM vw_campaign_kpis ORDER BY channel_code;"
```

## Paso 9 — Probar el agente de IA

Con Ollama corriendo:

```bash
python -c "from dotenv import load_dotenv; load_dotenv(); import sys; sys.path[:0]=['src/ai','src/data']; from agent import run_agent; import json; print(json.dumps(run_agent('¿Cuál es el ROI por canal?'), ensure_ascii=False, indent=2, default=str))"
```

---

## Variables de entorno

| Variable | Para qué sirve |
|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | Credenciales de la base (usuario administrador). |
| `POSTGRES_HOST` / `POSTGRES_PORT` | Dónde está la base (`localhost` en local, `db` dentro de Docker). |
| `POSTGRES_RO_USER` / `POSTGRES_RO_PASSWORD` | Usuario de solo lectura para el agente. |
| `OLLAMA_MODEL` | Qué modelo de IA usar (`qwen2.5:7b`). |
| `OLLAMA_BASE_URL` | Dónde está corriendo Ollama (`http://localhost:11434`). |

---

## Estructura del repositorio

```
insight-extractor/
├── src/
│   ├── data/
│   │   ├── models.py          # Las tablas, el histórico y la vista de KPIs
│   │   ├── pipeline.py        # La carga de datos y unificación de fuentes
│   │   └── validator.py       # El filtro de calidad (gobernanza)
│   ├── ai/
│   │   ├── agent.py           # El agente de IA (las tres estaciones)
│   │   └── tools.py           # El acceso seguro de solo lectura a la base
│   └── main.py                # La API web — Fase 3
├── data_sources/
│   ├── marketing_spend.csv    # Fuente B en formato archivo (CSV)
│   └── marketing_spend_api.json # Fuente B en formato API (JSON)
├── migrations/                # Migraciones de base de datos — Fase 3
├── docker-compose.yml         # Levanta la base de datos (y la app en Fase 3)
├── Dockerfile                 # Fase 3
├── requirements.txt           # Lista de dependencias de Python
├── .env.example               # Plantilla de configuración
├── README.md
└── ARCHITECTURE.md            # Diseño de escalado — Fase 3
```

---

## Estado del proyecto

- [x] **Fase 0 — Entorno:** Python, Git, Docker y Ollama configurados.
- [x] **Fase 1 — Datos y gobernanza:** dos fuentes (ventas + inversión en CSV y
      API), filtro de calidad completo, histórico de cambios en dos catálogos,
      tabla principal y KPIs funcionando de principio a fin.
- [x] **Seguridad — Usuario de solo lectura** creado y verificado.
- [x] **Fase 2 — Agente de IA:** las tres estaciones funcionando, doble candado de
      seguridad y manejo honesto de datos faltantes.
- [ ] **Fase 3 — API y despliegue:** endpoints FastAPI, Docker completo,
      migraciones y documento de arquitectura.
