from dotenv import load_dotenv
load_dotenv()
import sys, json
sys.path.insert(0, "src/data")
from pipeline import ingest_spend_from_csv, ingest_spend_from_api

# Fuente B - CSV
with open("data_sources/marketing_spend.csv") as f:
    print("CSV:", ingest_spend_from_csv(f.read()))

# Fuente B - API (JSON)
with open("data_sources/marketing_spend_api.json") as f:
    print("API:", ingest_spend_from_api(json.load(f)))