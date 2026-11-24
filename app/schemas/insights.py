# Geçmiş (history), breakout ve karşılaştırma (diff) endpoint'lerinin yanıt şemaları

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# --- History ---

class HistoryTopItem(BaseModel):
    """Bir taramadaki tek bir top arama."""

    rank: int
    query: str
    value: int


class HistoryRisingItem(BaseModel):
    """Bir taramadaki tek bir rising arama."""

    rank: int
    query: str
    value: int
    is_breakout: bool


class HistoryScan(BaseModel):
    """Bir ürünün tek bir taramadaki sonuçları."""

    job_id: int
    scanned_at: datetime
    geo: str
    timeframe: str
    top: list[HistoryTopItem]
    rising: list[HistoryRisingItem]


class HistoryRead(BaseModel):
    """Bir ürünün başarılı taramalarının en yeniden eskiye geçmişi."""

    keyword: str
    scans: list[HistoryScan]

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "keyword": "Camera Bags",
                "scans": [
                    {
                        "job_id": 7,
                        "scanned_at": "2026-11-23T13:40:00Z",
                        "geo": "TR",
                        "timeframe": "today 1-m",
                        "top": [{"rank": 1, "query": "camera bag", "value": 100}],
                        "rising": [
                            {"rank": 1, "query": "mindshift camera bags", "value": 800, "is_breakout": False}
                        ],
                    }
                ],
            }
        }
    )


# --- Breakouts ---

class BreakoutRead(BaseModel):
    """Bir taramada breakout sayılan rising arama."""

    seed_keyword: str
    query: str
    value: int
    job_id: int
    scanned_at: datetime

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "seed_keyword": "Wine Carriers",
                "query": "wine carriers and totes",
                "value": 180000,
                "job_id": 7,
                "scanned_at": "2026-11-23T13:40:00Z",
            }
        }
    )


# --- Diff ---

class DiffItem(BaseModel):
    """Listeye yeni giren veya listeden çıkan arama."""

    query: str
    value: int


class DiffChange(BaseModel):
    """İki taramada da bulunan ama değeri veya sırası değişen arama."""

    query: str
    old_value: int
    new_value: int
    delta: int = Field(description="new_value - old_value")
    old_rank: int
    new_rank: int


class TypeDiff(BaseModel):
    """Tek bir tür (top veya rising) için iki tarama arasındaki fark."""

    new: list[DiffItem] = Field(description="Yalnızca yeni taramada bulunan aramalar (değer: yeni taramadaki)")
    dropped: list[DiffItem] = Field(description="Yalnızca eski taramada bulunan aramalar (değer: eski taramadaki)")
    changed: list[DiffChange] = Field(description="Her iki taramada bulunan, değeri veya sırası değişen aramalar")


class KeywordDiff(BaseModel):
    """Bir seed keyword'ün iki taraması arasındaki fark."""

    keyword: str
    top: TypeDiff
    rising: TypeDiff


class DiffResponse(BaseModel):
    """İki tarama arasında, her ikisinde de başarılı olan ortak keyword'lerin karşılaştırması."""

    from_job: int
    to_job: int
    warnings: list[str] = Field(description="Karşılaştırmayı yanıltabilecek farklar (ör. farklı geo veya timeframe)")
    keywords: list[KeywordDiff]
    only_in_from: list[str] = Field(description="Yalnızca eski taramada başarılı olan keyword'ler")
    only_in_to: list[str] = Field(description="Yalnızca yeni taramada başarılı olan keyword'ler")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "from_job": 3,
                "to_job": 7,
                "warnings": ["geo differs: '' → 'TR'"],
                "keywords": [
                    {
                        "keyword": "Camera Bags",
                        "top": {
                            "new": [{"query": "small camera bags", "value": 40}],
                            "dropped": [{"query": "coach bags", "value": 22}],
                            "changed": [
                                {
                                    "query": "best camera bags",
                                    "old_value": 36,
                                    "new_value": 45,
                                    "delta": 9,
                                    "old_rank": 3,
                                    "new_rank": 2,
                                }
                            ],
                        },
                        "rising": {"new": [], "dropped": [], "changed": []},
                    }
                ],
                "only_in_from": ["Coasters"],
                "only_in_to": ["Aprons"],
            }
        }
    )
