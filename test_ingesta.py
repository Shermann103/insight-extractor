from dotenv import load_dotenv
load_dotenv()

import sys
sys.path.insert(0, "src/data")

from datetime import date
from pipeline import ingest_sales, ingest_channel_rates

# 1) Cargar tarifas de canal (alimenta el SCD2)
rates = [
    {"channel": "FB Ads", "base_cpc": 0.50, "valid_from": "2026-01-01"},
    {"channel": "google ads", "base_cpc": 0.80, "valid_from": "2026-01-01"},
]
print("Tarifas:", ingest_channel_rates(rates))

# 2) Ingesta de ventas (con un registro malo a propósito)
sales = [
    {"transaction_id": "t1", "customer_code": "c1", "amount": 100, "event_date": str(date.today()), "channel": "FB Ads", "clicks": 10, "is_new_customer": True},
    {"transaction_id": "t2", "customer_code": "c2", "amount": 50, "event_date": str(date.today()), "channel": "facebook", "clicks": 5, "is_new_customer": False},
    {"transaction_id": "t3", "customer_code": "c3", "amount": -20, "event_date": str(date.today()), "channel": "fb", "clicks": 2, "is_new_customer": False},
]
print("Ventas:", ingest_sales(sales))