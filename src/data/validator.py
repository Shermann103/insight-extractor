"""
validator.py — Capa de gobernanza / calidad de datos (Pydantic).

Responsabilidades:
  1. Unificación de canales: homologar variantes ("FB Ads", "facebook_ads",
     "Facebook") a un valor canónico ("FACEBOOK").
  2. Consistencia: rechazar montos negativos y fechas futuras.
  3. Entregar registros limpios y tipados para que pipeline.py los cargue.

La deduplicación NO vive aquí: es responsabilidad del pipeline + la restricción
UNIQUE de la base de datos (una fila por fecha+canal).
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, field_validator, model_validator


# ---------------------------------------------------------------------------
# Unificación de canales
# ---------------------------------------------------------------------------
# Mapa de variante -> valor canónico. Se compara en minúsculas y sin espacios
# sobrantes, de modo que "FB Ads" y "fb   ads" se resuelven igual.
CHANNEL_MAP: dict[str, str] = {
    "fb ads": "FACEBOOK",
    "fb_ads": "FACEBOOK",
    "facebook_ads": "FACEBOOK",
    "facebook ads": "FACEBOOK",
    "facebook": "FACEBOOK",
    "fb": "FACEBOOK",
    "google ads": "GOOGLE",
    "google_ads": "GOOGLE",
    "googleads": "GOOGLE",
    "google": "GOOGLE",
    "adwords": "GOOGLE",
    "ig": "INSTAGRAM",
    "instagram": "INSTAGRAM",
    "instagram_ads": "INSTAGRAM",
    "tiktok": "TIKTOK",
    "tiktok_ads": "TIKTOK",
    "tik tok": "TIKTOK",
}


def normalize_channel(raw: str) -> str:
    """
    Convierte un nombre de canal crudo a su forma canónica.

    Si no está en el mapa, devuelve el valor en mayúsculas y con guiones bajos
    (mejor no perder el dato: se marca como 'desconocido pero estandarizado').
    """
    if raw is None:
        raise ValueError("El canal no puede ser nulo")
    key = " ".join(raw.strip().lower().split())  # colapsa espacios múltiples
    if key in CHANNEL_MAP:
        return CHANNEL_MAP[key]
    return key.upper().replace(" ", "_")


# ---------------------------------------------------------------------------
# Esquema de un registro de venta crudo (Fuente A - transaccional)
# ---------------------------------------------------------------------------
class RawSalesRecord(BaseModel):
    """
    Representa una fila cruda de ventas antes de consolidar.

    Pydantic valida y transforma automáticamente:
      - normaliza el canal,
      - rechaza montos negativos,
      - rechaza fechas futuras.
    Si algún registro no cumple, Pydantic lanza ValidationError y el pipeline
    lo cuenta como 'rechazado' (no rompe todo el lote).
    """
    transaction_id: str
    customer_code: str
    amount: float
    event_date: date
    channel: str
    clicks: int = 0
    is_new_customer: bool = False

    @field_validator("channel")
    @classmethod
    def _normalize_channel(cls, v: str) -> str:
        return normalize_channel(v)

    @field_validator("amount")
    @classmethod
    def _no_negative_amount(cls, v: float) -> float:
        if v < 0:
            raise ValueError(f"Monto negativo no permitido: {v}")
        return v

    @field_validator("clicks")
    @classmethod
    def _no_negative_clicks(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"Clics negativos no permitidos: {v}")
        return v

    @model_validator(mode="after")
    def _no_future_dates(self) -> "RawSalesRecord":
        if self.event_date > date.today():
            raise ValueError(f"Fecha futura no permitida: {self.event_date}")
        return self


# ---------------------------------------------------------------------------
# Esquema de una tarifa de canal (para alimentar el SCD tipo 2)
# ---------------------------------------------------------------------------
class RawChannelRate(BaseModel):
    """Tarifa (CPC) de un canal en una fecha dada. Alimenta el histórico SCD2."""
    channel: str
    base_cpc: float
    valid_from: date

    @field_validator("channel")
    @classmethod
    def _normalize_channel(cls, v: str) -> str:
        return normalize_channel(v)

    @field_validator("base_cpc")
    @classmethod
    def _positive_cpc(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"CPC debe ser positivo: {v}")
        return v

    @model_validator(mode="after")
    def _no_future_dates(self) -> "RawChannelRate":
        if self.valid_from > date.today():
            raise ValueError(f"Fecha futura no permitida: {self.valid_from}")
        return self
