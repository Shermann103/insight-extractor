"""
seed_demo.py — Limpia y recarga la base con datos de demostración creíbles.

Crea una historia coherente para que los KPIs sean interesantes:
  - Facebook  → canal estrella (ROI alto)
  - Google    → sólido
  - Instagram → flojo pero positivo
  - TikTok    → pierde dinero (ROI negativo)

Los datos se reparten en 3 días (2026-08-01 a 2026-08-03) e ingresan por las
DOS fuentes (ventas por Fuente A, inversión por Fuente B), tal como en producción.

Uso:
    python seed_demo.py
"""

from dotenv import load_dotenv

load_dotenv()

import sys

sys.path.insert(0, "src/data")

from sqlalchemy import text  # noqa: E402

from models import SessionLocal, engine  # noqa: E402
from pipeline import (  # noqa: E402
    ingest_channel_rates,
    ingest_sales,
    ingest_spend_from_api,
)


def wipe() -> None:
    """Vacía las tablas para empezar limpio (respeta las llaves foráneas)."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE fact_campaign_performance, dim_channel, dim_customer "
                "RESTART IDENTITY CASCADE"
            )
        )
    print("Tablas vaciadas.")


# --- Tarifas de canal (alimentan el SCD tipo 2) ---
CHANNEL_RATES = [
    {"channel": "Facebook", "base_cpc": 0.50, "valid_from": "2026-01-01"},
    {"channel": "Google", "base_cpc": 0.80, "valid_from": "2026-01-01"},
    {"channel": "Instagram", "base_cpc": 0.45, "valid_from": "2026-01-01"},
    {"channel": "TikTok", "base_cpc": 0.35, "valid_from": "2026-01-01"},
]

# --- Fuente A: ventas (repartidas en 3 días, con variantes de nombre de canal) ---
# Cada entrada agrega ventas; el pipeline las consolida por (fecha, canal).
def build_sales() -> list[dict]:
    sales = []
    # (canal_variante, fecha, monto, clics, es_nuevo) por transacción-grupo
    plan = [
        # Facebook: estrella — 40 tx, 1200 ventas, 25 nuevos, 900 clics
        ("FB Ads", "2026-08-01", 400, 300, 8), ("facebook", "2026-08-02", 400, 300, 9),
        ("Facebook", "2026-08-03", 400, 300, 8),
        # Google: sólido — 30 tx, 900 ventas, 18 nuevos, 750 clics
        ("google ads", "2026-08-01", 300, 250, 6), ("Google", "2026-08-02", 300, 250, 6),
        ("google_ads", "2026-08-03", 300, 250, 6),
        # Instagram: flojo — 15 tx, 400 ventas, 8 nuevos, 600 clics
        ("ig", "2026-08-01", 130, 200, 3), ("Instagram", "2026-08-02", 140, 200, 3),
        ("instagram", "2026-08-03", 130, 200, 2),
        # TikTok: pierde — 6 tx, 150 ventas, 4 nuevos, 500 clics
        ("tiktok", "2026-08-01", 50, 170, 1), ("TikTok", "2026-08-02", 50, 170, 2),
        ("tik tok", "2026-08-03", 50, 160, 1),
    ]
    # nº de transacciones por grupo para repartir (aprox el total deseado)
    tx_por_grupo = {
        "2026-08-01": {"FB Ads": 14, "google ads": 10, "ig": 5, "tiktok": 2},
        "2026-08-02": {"facebook": 13, "Google": 10, "Instagram": 5, "TikTok": 2},
        "2026-08-03": {"Facebook": 13, "google_ads": 10, "instagram": 5, "tik tok": 2},
    }
    for canal, fecha, monto, clics, nuevos in plan:
        n_tx = tx_por_grupo[fecha][canal]
        # Repartimos el monto y los clics entre las transacciones del grupo
        for i in range(n_tx):
            sales.append(
                {
                    "transaction_id": f"{canal}-{fecha}-{i}",
                    "customer_code": f"cust-{canal}-{fecha}-{i}",
                    "amount": round(monto / n_tx, 2),
                    "event_date": fecha,
                    "channel": canal,
                    "clicks": clics // n_tx,
                    "is_new_customer": i < nuevos,
                }
            )
    return sales


# --- Fuente B: inversión (simula respuesta de API de plataformas de ads) ---
SPEND_API = [
    # Facebook: 300 inversión total
    {"event_date": "2026-08-01", "channel": "FB Ads", "spend": 100, "impressions": 12000, "clicks": 0},
    {"event_date": "2026-08-02", "channel": "facebook", "spend": 100, "impressions": 12000, "clicks": 0},
    {"event_date": "2026-08-03", "channel": "Facebook", "spend": 100, "impressions": 12000, "clicks": 0},
    # Google: 350
    {"event_date": "2026-08-01", "channel": "Google Ads", "spend": 120, "impressions": 9000, "clicks": 0},
    {"event_date": "2026-08-02", "channel": "google", "spend": 115, "impressions": 9000, "clicks": 0},
    {"event_date": "2026-08-03", "channel": "google_ads", "spend": 115, "impressions": 9000, "clicks": 0},
    # Instagram: 250
    {"event_date": "2026-08-01", "channel": "Instagram", "spend": 85, "impressions": 7000, "clicks": 0},
    {"event_date": "2026-08-02", "channel": "ig", "spend": 85, "impressions": 7000, "clicks": 0},
    {"event_date": "2026-08-03", "channel": "instagram", "spend": 80, "impressions": 7000, "clicks": 0},
    # TikTok: 200
    {"event_date": "2026-08-01", "channel": "TikTok", "spend": 70, "impressions": 5000, "clicks": 0},
    {"event_date": "2026-08-02", "channel": "tiktok", "spend": 70, "impressions": 5000, "clicks": 0},
    {"event_date": "2026-08-03", "channel": "tik tok", "spend": 60, "impressions": 5000, "clicks": 0},
]


def main() -> None:
    wipe()
    print("Cargando tarifas de canal (SCD2)...")
    print(" ", ingest_channel_rates(CHANNEL_RATES))
    print("Cargando ventas (Fuente A)...")
    print(" ", ingest_sales(build_sales()))
    print("Cargando inversión (Fuente B, API)...")
    print(" ", ingest_spend_from_api(SPEND_API))
    print("\nDemo cargada. Consulta los KPIs con /metrics o con el agente.")


if __name__ == "__main__":
    main()
