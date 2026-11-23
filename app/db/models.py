# Veritabanı tabloları: ScrapeJob → JobKeyword → QueryResult
# Eski scraper'ın CSV ve failed_keywords.log çıktılarının ilişkisel karşılığı

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator
from sqlmodel import Field, Relationship, SQLModel


# --- Enum'lar ---
# Üye adları bilinçli olarak küçük harf: SQLAlchemy enum'ları veritabanına üye ADIYLA yazar;
# ad ile değer aynı olunca veritabanında da API'de de aynı metin ("pending") görünür

class JobStatus(str, Enum):
    """Bir tarama işinin yaşam döngüsü."""

    pending = "pending"
    running = "running"
    completed = "completed"
    partial = "partial"
    failed = "failed"


class KeywordStatus(str, Enum):
    """Tek bir seed keyword'ün tarama sonucu."""

    pending = "pending"
    success = "success"
    failed = "failed"


class QueryType(str, Enum):
    """İlişkili arama türü: en çok aranan veya yükselişteki."""

    top = "top"
    rising = "rising"


# --- Zaman damgası yardımcıları ---

def utc_now() -> datetime:
    """Şu anki zamanı UTC ve timezone bilgili olarak döner."""
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Zaman damgalarını her zaman UTC olarak saklayan ve UTC bilgili döndüren kolon tipi.

    SQLite timezone bilgisini saklamaz ve değeri naive döndürür; PostgreSQL ise bilgili döndürür.
    Bu tip iki veritabanında da aynı davranışı garanti eder.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        """Veritabanına yazmadan önce değeri UTC'ye çevirir."""
        # Naive değer UTC kabul edilir, bilgili değer UTC'ye dönüştürülür
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        """Veritabanından okunan değeri UTC bilgili hale getirir."""
        # SQLite'tan gelen naive değere UTC eklenir
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


# --- Tablolar ---

class ScrapeJob(SQLModel, table=True):
    """Kullanıcının başlattığı tek bir tarama işi."""

    __tablename__ = "scrape_jobs"

    id: int | None = Field(default=None, primary_key=True)
    status: JobStatus = Field(default=JobStatus.pending, index=True)

    # Tarama parametreleri: geo "" (tüm dünya) veya 2 harfli ülke kodu
    geo: str
    timeframe: str

    # İlerleme göstergesi
    total_keywords: int
    processed_keywords: int = Field(default=0)

    # Job seviyesinde beklenmeyen hata
    error_message: str | None = Field(default=None)

    # Zaman damgaları (UTC)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    started_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    finished_at: datetime | None = Field(default=None, sa_type=UTCDateTime)

    # Job silinince keyword'leri de silinir
    keywords: list["JobKeyword"] = Relationship(
        back_populates="job",
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "order_by": "JobKeyword.position"},
    )


class JobKeyword(SQLModel, table=True):
    """Bir tarama işindeki tek bir seed keyword ve onun durumu."""

    __tablename__ = "job_keywords"

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="scrape_jobs.id", index=True)

    # Kullanıcının yazdığı hali (ör. "Camera Bags") ve history sorguları için normalize hali
    keyword: str
    keyword_normalized: str = Field(index=True)

    # İstekteki sıra
    position: int

    # Eski failed_keywords.log satırının karşılığı: status=failed + error_message
    status: KeywordStatus = Field(default=KeywordStatus.pending)
    error_message: str | None = Field(default=None)
    processed_at: datetime | None = Field(default=None, sa_type=UTCDateTime)

    job: ScrapeJob = Relationship(back_populates="keywords")

    # Keyword silinince sonuçları da silinir
    results: list["QueryResult"] = Relationship(
        back_populates="job_keyword",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class QueryResult(SQLModel, table=True):
    """Bir seed keyword için Google Trends'in döndürdüğü tek bir ilişkili arama."""

    __tablename__ = "query_results"

    id: int | None = Field(default=None, primary_key=True)
    job_keyword_id: int = Field(foreign_key="job_keywords.id", index=True)

    type: QueryType

    # Google'ın döndürdüğü sıra (1'den başlar)
    rank: int
    query: str

    # top: 0–100 ilgi skoru, rising: yüzde artış
    value: int

    # type == rising ve value >= BREAKOUT_THRESHOLD
    is_breakout: bool = Field(default=False)

    job_keyword: JobKeyword = Relationship(back_populates="results")
