# Imagen base ligera de Python.
FROM python:3.12-slim

# Evita que Python genere .pyc y fuerza logs sin buffer (mejor en contenedores).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencias del sistema necesarias para psycopg2 (driver de PostgreSQL).
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependencias de Python primero (aprovecha la caché de capas).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el resto del proyecto.
COPY . .

# El script de arranque espera la BD, corre migraciones y lanza la API.
RUN chmod +x /app/entrypoint.sh

EXPOSE 8000

CMD ["/app/entrypoint.sh"]
