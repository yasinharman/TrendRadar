# Birden çok endpoint'in paylaştığı yanıt şemaları

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

from app.core.config import TrendsMode

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Listeleme endpoint'lerinin ortak zarfı: sayfadaki kayıtlar ve toplam sayı."""

    items: list[T]
    total: int
    limit: int
    offset: int


class ErrorResponse(BaseModel):
    """FastAPI'nin standart hata gövdesi."""

    detail: str

    model_config = ConfigDict(json_schema_extra={"example": {"detail": "Job bulunamadı"}})


def error_response(description: str, detail: str) -> dict:
    """Swagger'da bir hata yanıtını, o duruma özgü örnek mesajıyla belgeleyen tanımı üretir."""
    return {
        "model": ErrorResponse,
        "description": description,
        "content": {"application/json": {"example": {"detail": detail}}},
    }


class QueueInfo(BaseModel):
    """Tarama kuyruğunun anlık durumu."""

    pending: int
    running: int


class HealthResponse(BaseModel):
    """Servisin ayakta olduğunu ve hangi ayarlarla çalıştığını gösterir."""

    status: str
    app: str
    mode: TrendsMode
    database: str
    queue: QueueInfo

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "ok",
                "app": "TrendRadar API",
                "mode": "mock",
                "database": "sqlite",
                "queue": {"pending": 1, "running": 1},
            }
        }
    )
