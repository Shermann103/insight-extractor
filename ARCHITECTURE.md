# Arquitectura y escalabilidad

Este documento describe cómo se llevaría el sistema **Insight-Extractor** desde su
versión local (Docker Compose) hasta un despliegue en la nube listo para
producción, y cómo se estructuraría el pipeline de CI/CD.

El diseño local ya separa responsabilidades (ingesta/gobernanza, agente de IA,
API), lo que facilita mapear cada pieza a un servicio gestionado en la nube.

---

## 1. Escalado en Google Cloud Platform (GCP)

La idea es reemplazar cada componente local por un servicio gestionado que escale
solo y reduzca el mantenimiento.

### Mapeo de componentes

| Componente local | Servicio en GCP | Función |
|---|---|---|
| API FastAPI (contenedor) | **Cloud Run** | Ejecuta la API en contenedores que escalan automáticamente (incluso a cero). |
| PostgreSQL (contenedor) | **Cloud SQL for PostgreSQL** | Base transaccional gestionada, con backups y alta disponibilidad. |
| Analítica a gran escala | **BigQuery** | Almacén analítico para históricos masivos y consultas de KPIs sobre millones de filas. |
| Gobernanza / transformación | **dbt** | Versiona y prueba las transformaciones y los KPIs como código. |
| Agente de IA (Ollama local) | **Vertex AI** | Sirve el modelo de lenguaje de forma gestionada y escalable. |

### Cómo encaja cada uno

**Cloud Run (la API).** El `Dockerfile` que ya tenemos se despliega tal cual.
Cloud Run levanta más instancias cuando sube el tráfico y las baja cuando
disminuye, cobrando solo por uso. Es la evolución natural de nuestro contenedor
`api`.

**Cloud SQL (la base transaccional).** Reemplaza al contenedor de PostgreSQL sin
cambiar el código: seguimos usando SQLAlchemy y la misma URL de conexión, solo
cambia el host. Aquí viven las tablas operativas (dimensiones, hechos) y el
usuario de solo lectura.

**BigQuery (la analítica).** A medida que el histórico crece a millones de filas,
las consultas de KPIs pesan sobre la base transaccional. La solución es replicar
los datos hacia BigQuery (un almacén columnar hecho para analítica) y calcular ahí
los KPIs pesados. Cloud SQL queda para la operación del día a día; BigQuery para
los análisis grandes.

**dbt (la gobernanza como código).** Nuestra lógica de KPIs (la vista
`vw_campaign_kpis`) y las reglas de calidad se migrarían a modelos de dbt. La
ventaja: las transformaciones quedan versionadas en Git, con pruebas automáticas
de calidad de datos (no nulos, unicidad, rangos válidos) que se ejecutan en cada
cambio. Es la versión "empresarial" de nuestra capa de gobernanza actual.

**Vertex AI (el modelo de IA).** En local usamos Ollama por costo cero. En
producción, el modelo se serviría desde Vertex AI, que ofrece un endpoint
gestionado, escalable y con control de acceso. El agente LangGraph no cambiaría su
lógica; solo apuntaría a ese endpoint en lugar de a Ollama.

### Flujo de datos en la nube

```
Fuentes (ventas, inversión)
        │
        ▼
   Cloud Run (API FastAPI)  ──►  Cloud SQL (operacional)
        │                            │
        │                            ▼
        │                    Replicación / ELT
        │                            │
        │                            ▼
        │                     BigQuery (analítica) ◄── dbt (KPIs + calidad)
        ▼
   Agente LangGraph  ──►  Vertex AI (LLM gestionado)
```

---

## 2. Pipeline de CI/CD en Azure DevOps

El objetivo es desplegar de forma **segura** dos cosas delicadas: el código del
agente/API y, sobre todo, las **migraciones de base de datos** (que si se hacen
mal, pueden romper datos en producción).

### Etapas del pipeline

**1. CI — Integración continua (en cada push / pull request):**

- Instalar dependencias y ejecutar los tests (validación de gobernanza, lógica
  del agente, seguridad de la lista blanca).
- Análisis estático y de seguridad del código.
- Construir la imagen Docker y publicarla en un registro de contenedores
  (Azure Container Registry).

**2. CD — Despliegue (tras aprobación):**

- **Migraciones primero, con red de seguridad.** Antes de desplegar el código
  nuevo, correr `alembic upgrade head` contra la base. Como las migraciones son
  el paso más riesgoso, se ejecutan en un *job* aislado que:
  - primero corre contra un entorno de *staging* (copia de producción),
  - hace un *backup* de la base de producción antes de aplicar nada,
  - solo entonces aplica la migración en producción.
- **Despliegue del código.** Una vez la base está migrada, se despliega la nueva
  imagen a Cloud Run (o al destino que sea) con una estrategia gradual
  (*canary* o *blue-green*): el tráfico se mueve poco a poco a la versión nueva,
  y si algo falla, se revierte automáticamente.

### Seguridad y buenas prácticas

- **Separación de entornos:** dev → staging → producción, cada uno con su base y
  sus credenciales.
- **Secretos fuera del código:** las contraseñas y claves se guardan en un gestor
  de secretos (Azure Key Vault), nunca en el repositorio.
- **Aprobaciones manuales:** el paso a producción requiere aprobación humana.
- **Reversibilidad:** cada migración de Alembic tiene su `downgrade()`, y cada
  despliegue puede revertirse a la versión anterior.

### Diagrama del pipeline

```
  Push a Git
      │
      ▼
  ┌─────────────┐   tests + build de imagen
  │     CI      │
  └──────┬──────┘
         │  imagen publicada en el registro
         ▼
  ┌─────────────┐   1. backup de la BD
  │  CD: BD     │   2. alembic upgrade en staging
  │ migraciones │   3. alembic upgrade en producción
  └──────┬──────┘
         │
         ▼
  ┌─────────────┐   despliegue gradual (canary/blue-green)
  │  CD: app    │   con reversión automática si falla
  └─────────────┘
```

---

## Resumen

El sistema local ya está diseñado con las fronteras correctas (API, datos, IA
separados), por lo que escalar consiste en **reemplazar cada pieza por su
equivalente gestionado** en GCP, sin reescribir la lógica de negocio. El pipeline
de Azure DevOps prioriza la seguridad de las migraciones de base de datos, que son
el punto más delicado de cualquier despliegue con estado.
