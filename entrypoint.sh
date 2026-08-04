#!/bin/sh
# entrypoint.sh — Arranque de la aplicación dentro del contenedor.
# 1. Espera a que PostgreSQL esté listo.
# 2. Ejecuta las migraciones de Alembic (crea tablas y vista).
# 3. Inicia el servidor FastAPI con uvicorn.
set -e

echo "Esperando a PostgreSQL en ${POSTGRES_HOST}:${POSTGRES_PORT}..."
until python -c "import socket,os,sys; s=socket.socket(); s.settimeout(2); s.connect((os.getenv('POSTGRES_HOST','db'), int(os.getenv('POSTGRES_PORT','5432')))); s.close()" 2>/dev/null; do
  echo "  ...BD no disponible todavía, reintentando en 2s"
  sleep 2
done
echo "PostgreSQL está listo."

echo "Ejecutando migraciones de Alembic..."
alembic upgrade head

echo "Iniciando la API FastAPI..."
exec uvicorn main:app --app-dir src --host 0.0.0.0 --port 8000