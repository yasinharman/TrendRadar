# Tarama işi (job) endpoint'lerinin istek ve yanıt şemaları

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import GEO_PATTERN, Timeframe
from app.db.models import JobStatus, KeywordStatus
from app.services.trends.base import normalize_keyword


# --- İstek ---

class JobCreate(BaseModel):
    """Yeni tarama isteği: bir veya birden çok ürün adı, isteğe bağlı ülke ve zaman aralığı."""

    keywords: list[str] = Field(
        min_length=1,
        description="Taranacak ürün adları. Boşluklar sadeleştirilir, büyük/küçük harf farkı gözetmeden "
        "tekrarlar temizlenir (ilk yazılış korunur).",
    )
    geo: str | None = Field(
        default=None,
        description='2 harfli ülke kodu (ör. "TR") veya tüm dünya için boş string. '
        "Verilmezse sunucunun DEFAULT_GEO ayarı kullanılır.",
    )
    timeframe: Timeframe | None = Field(
        default=None,
        description="Zaman aralığı. Verilmezse sunucunun DEFAULT_TIMEFRAME ayarı kullanılır.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"keywords": ["Camera Bags", "Backpacks", "Tote Bags"], "geo": "TR", "timeframe": "today 1-m"}
        }
    )

    @field_validator("keywords")
    @classmethod
    def clean_keywords(cls, keywords: list[str]) -> list[str]:
        """Boşlukları sadeleştirir, boş keyword'ü reddeder ve tekrarları temizler."""
        cleaned: list[str] = []
        seen: set[str] = set()
        for keyword in keywords:
            # Baştaki/sondaki boşlukları at, aradaki boşlukları teke indir
            display = " ".join(keyword.split())
            if not display:
                raise ValueError("Keyword cannot be empty")
            # Boşluklar sadeleştirildi

            # Aynı keyword'ün farklı yazılışlarından yalnızca ilki tutulur
            normalized = normalize_keyword(display)
            if normalized in seen:
                continue
            seen.add(normalized)
            cleaned.append(display)
            # Tekrar kontrolü tamamlandı
        return cleaned

    @field_validator("geo")
    @classmethod
    def normalize_geo(cls, geo: str | None) -> str | None:
        """Ülke kodunu büyük harfe çevirir ve biçimini doğrular."""
        if geo is None:
            return None
        geo = geo.strip().upper()
        if not re.fullmatch(GEO_PATTERN, geo):
            raise ValueError('Country code must be 2 letters (e.g. "TR") or empty for worldwide')
        return geo


# --- Yanıtlar ---

class JobRead(BaseModel):
    """Bir tarama işinin durumu ve ilerlemesi."""

    id: int
    status: JobStatus
    geo: str
    timeframe: str
    total_keywords: int
    processed_keywords: int
    progress: str = Field(description='İşlenen / toplam keyword (ör. "2/3")')
    queue_position: int | None = Field(
        description="Yalnızca pending iken dolu: bu işten önce bekleyen ve çalışan iş sayısı (0 = sıradaki)"
    )
    error_message: str | None = Field(description="İş seviyesinde beklenmeyen hata; normalde null")
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": 12,
                "status": "pending",
                "geo": "TR",
                "timeframe": "today 1-m",
                "total_keywords": 3,
                "processed_keywords": 0,
                "progress": "0/3",
                "queue_position": 1,
                "error_message": None,
                "created_at": "2026-11-23T13:30:00Z",
                "started_at": None,
                "finished_at": None,
            }
        }
    )


class KeywordSummary(BaseModel):
    """Bir iş içindeki tek keyword'ün sonucu."""

    keyword: str
    status: KeywordStatus
    top_count: int
    rising_count: int
    error_message: str | None


class JobDetail(JobRead):
    """İş bilgileri ve keyword bazında özet."""

    keywords: list[KeywordSummary]

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": 12,
                "status": "partial",
                "geo": "TR",
                "timeframe": "today 1-m",
                "total_keywords": 2,
                "processed_keywords": 2,
                "progress": "2/2",
                "queue_position": None,
                "error_message": None,
                "created_at": "2026-11-23T13:30:00Z",
                "started_at": "2026-11-23T13:30:01Z",
                "finished_at": "2026-11-23T13:30:20Z",
                "keywords": [
                    {
                        "keyword": "Camera Bags",
                        "status": "success",
                        "top_count": 5,
                        "rising_count": 3,
                        "error_message": None,
                    },
                    {
                        "keyword": "Media Organizers",
                        "status": "failed",
                        "top_count": 0,
                        "rising_count": 0,
                        "error_message": "EmptyTrendsError: Empty data returned (both top and rising are empty)",
                    },
                ],
            }
        }
    )
