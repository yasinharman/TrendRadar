# Tarama sonuçları endpoint'lerinin filtre ve yanıt şemaları

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import QueryType


class ResultFilters(BaseModel):
    """GET /jobs/{id}/results için query string filtreleri; hepsi isteğe bağlıdır ve birlikte kullanılabilir."""

    type: QueryType | None = Field(default=None, description="Yalnızca top veya yalnızca rising sonuçlar.")
    keyword: str | None = Field(
        default=None, description="Yalnızca bu seed keyword'ün sonuçları (büyük/küçük harf duyarsız)."
    )
    min_value: int | None = Field(default=None, ge=0, description="Değeri en az bu kadar olan sonuçlar.")
    breakout_only: bool = Field(default=False, description="Yalnızca breakout sayılan rising sonuçlar.")
    search: str | None = Field(
        default=None, description="Sorgu metninde geçen ifade (büyük/küçük harf duyarsız)."
    )


class QueryResultRead(BaseModel):
    """Bir seed keyword için bulunan tek bir ilişkili arama."""

    seed_keyword: str
    type: QueryType
    rank: int
    query: str
    value: int = Field(description="top: 0–100 ilgi skoru, rising: yüzde artış")
    is_breakout: bool

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "seed_keyword": "Camera Bags",
                "type": "rising",
                "rank": 1,
                "query": "mindshift camera bags",
                "value": 800,
                "is_breakout": False,
            }
        }
    )
