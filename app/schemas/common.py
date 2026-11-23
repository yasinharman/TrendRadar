# Birden çok endpoint'in paylaştığı yanıt şemaları

from pydantic import BaseModel, ConfigDict

from app.core.config import TrendsMode


class ErrorResponse(BaseModel):
    """FastAPI'nin standart hata gövdesi."""

    detail: str

    model_config = ConfigDict(json_schema_extra={"example": {"detail": "Veritabanına ulaşılamıyor"}})


class HealthResponse(BaseModel):
    """Servisin ayakta olduğunu ve hangi ayarlarla çalıştığını gösterir."""

    status: str
    app: str
    mode: TrendsMode
    database: str

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"status": "ok", "app": "TrendRadar API", "mode": "mock", "database": "sqlite"}
        }
    )
