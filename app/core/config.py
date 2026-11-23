# TrendRadar API için tüm konfigürasyon değerleri
# Magic number kullanılmaz; tüm sayısal sabitler ve ayarlar Settings sınıfında tanımlanır

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Çalışma modları: mock = sahte veri, live = gerçek Google Trends
TrendsMode = Literal["mock", "live"]

# Google Trends'in kabul ettiği zaman aralıkları (Swagger'da dropdown olarak görünür)
Timeframe = Literal["now 7-d", "today 1-m", "today 3-m", "today 12-m", "today 5-y"]

# Ülke kodu deseni: boş string (tüm dünya) veya 2 büyük harf
GEO_PATTERN = r"^([A-Z]{2})?$"


class Settings(BaseSettings):
    """Uygulama ayarları; değerler `.env` dosyasından veya ortam değişkenlerinden okunur."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Uygulama
    APP_NAME: str = "TrendRadar API"
    TRENDS_MODE: TrendsMode = "mock"
    # Uygulama ayarları tanımlandı

    # Veritabanı (Docker'da postgresql+psycopg://...)
    DATABASE_URL: str = "sqlite:///./trendradar.db"
    # Veritabanı ayarı tanımlandı

    # Google Trends oturumu ve locale — yalnızca live modda kullanılır
    # hl=en-US ile bazı keyword'lerde Google boş veri döndürüyor; tr-TR ile dönüyor
    COOKIE_FILE: str = "cookies.json"
    HL: str = "tr-TR"
    TZ_OFFSET: str = "-180"  # Türkiye (UTC+3), Google JS konvansiyonu: dakika cinsinden ters işaretli
    # Oturum/locale ayarları tanımlandı

    # Tarama isteğinde belirtilmezse kullanılan varsayılanlar
    DEFAULT_GEO: str = Field(default="", pattern=GEO_PATTERN)  # Boş string = tüm dünya
    DEFAULT_TIMEFRAME: Timeframe = "today 1-m"
    # Tarama varsayılanları tanımlandı

    # İstekler arası bekleme aralığı (saniye, rastgele jitter için)
    SLEEP_MIN: float = Field(default=5, ge=0)
    SLEEP_MAX: float = Field(default=12, ge=0)
    # Rate-limit bekleme aralığı tanımlandı

    # 429 sonrası exponential backoff ayarları
    MAX_RETRIES: int = Field(default=3, ge=1)
    BACKOFF_BASE: float = Field(default=30, ge=0)  # Saniye. Denemeler: 30, 60, 120
    REQUEST_TIMEOUT: float = Field(default=30, gt=0)  # Saniye
    # Retry ve backoff parametreleri tanımlandı

    # Rising değeri bu eşik ve üzerindeyse breakout sayılır
    BREAKOUT_THRESHOLD: int = Field(default=5000, ge=1)
    # Analiz eşiği tanımlandı

    # İstek sınırları
    MAX_KEYWORDS_PER_JOB: int = Field(default=30, ge=1)
    MAX_KEYWORD_LENGTH: int = Field(default=100, ge=1)
    DEFAULT_PAGE_SIZE: int = Field(default=50, ge=1)
    MAX_PAGE_SIZE: int = Field(default=200, ge=1)
    # İstek sınırları tanımlandı

    # Mock modda keyword başına bekleme (saniye); testlerde 0
    MOCK_DELAY_SECONDS: float = Field(default=0.5, ge=0)
    # Mock mod ayarı tanımlandı

    @model_validator(mode="after")
    def check_ranges(self) -> "Settings":
        """Birbirine bağlı ayar çiftlerinin tutarlı olduğunu doğrular."""
        # Alt sınır üst sınırı geçemez
        if self.SLEEP_MIN > self.SLEEP_MAX:
            raise ValueError("SLEEP_MIN, SLEEP_MAX değerinden büyük olamaz")
        if self.DEFAULT_PAGE_SIZE > self.MAX_PAGE_SIZE:
            raise ValueError("DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE değerinden büyük olamaz")
        # Aralık kontrolleri tamamlandı
        return self


@lru_cache
def get_settings() -> Settings:
    """Ayarları bir kez okuyup önbelleğe alır; sonraki çağrılar aynı nesneyi döner."""
    return Settings()
