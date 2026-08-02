from dotenv import load_dotenv
load_dotenv()
import sys, json
from datetime import date
sys.path.insert(0, "src/data")
from pipeline import ingest_sales, ingest_spend_from_csv, ingest_spend_from_api

# Fuente A: ventas (incluye un registro invalido a proposito)
sales = [
    {"transaction_id": "t1", "customer_code": "c1", "amount": 100, "event_date": str(date.today()), "channel": "FB Ads", "clicks": 10, "is_new_customer": True},
    {"transaction_id": "t2", "customer_code": "c2", "amount": 50, "event_date": str(date.today()), "channel": "facebook", "clicks": 5, "is_new_customer": True},
    {"transaction_id": "t3", "customer_code": "c3", "amount": -20, "event_date": str(date.today()), "channel": "fb", "clicks": 2, "is_new_customer": False},
]
print("Ventas (Fuente A):", ingest_sales(sales))

# Fuente B: inversion por CSV y por API
with open("data_sources/marketing_spend.csv") as f:
    print("Inversion CSV (Fuente B):", ingest_spend_from_csv(f.read()))
with open("data_sources/marketing_spend_api.json") as f:
    print("Inversion API (Fuente B):", ingest_spend_from_api(json.load(f)))