# TrendRadar API — uygulama giriş noktası
# create_app() factory'si ayarları ve trends client'ı dışarıdan alabilir; böylece testler kendi
# ayarlarını ve sahte client'ını enjekte eder

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.core.auth import BasicAuthMiddleware
from app.core.config import Settings, get_settings
from app.db.session import create_db_engine, init_db
from app.routers import health, insights, jobs, keywords, ui
from app.services.job_runner import recover_interrupted_jobs, resume_pending_jobs
from app.services.trends.base import TrendsClient
from app.services.trends.live_client import LiveTrendsClient
from app.services.trends.mock_client import MockTrendsClient

logger = logging.getLogger(__name__)

# Swagger arayüzünde gösterilen açıklama
APP_DESCRIPTION = (
    "Ürün isimleri için Google Trends'ten en çok aranan (top) ve yükselişteki (rising) "
    "ilişkili aramaları toplayan, saklayan ve analiz eden REST API."
)

# Swagger'da endpoint gruplarının açıklamaları
OPENAPI_TAGS = [
    {"name": "health", "description": "Servisin, veritabanının ve tarama kuyruğunun durumu."},
    {
        "name": "jobs",
        "description": "Tarama işleri: başlatma, ilerlemeyi izleme, sonuçları filtreleme ve CSV olarak indirme.",
    },
    {"name": "keywords", "description": "Bir ürünün farklı zamanlardaki taramalarının geçmişi."},
    {"name": "insights", "description": "Taramalar üzerinde analiz: breakout aramalar ve iki tarama arasındaki fark."},
]

# Çalışma modu adı (Settings.TRENDS_MODE ile karşılaştırılır)
LIVE_MODE = "live"


def build_trends_client(settings: Settings) -> TrendsClient:
    """TRENDS_MODE'a göre mock veya live client oluşturur."""
    # Mock mod cookie gerektirmez
    if settings.TRENDS_MODE != LIVE_MODE:
        return MockTrendsClient(settings)
    # Mod belirlendi

    # Live modda cookie dosyası yoksa veya geçersizse uygulama açık bir mesajla başlamaz (fail-fast)
    try:
        return LiveTrendsClient(settings)
    except (FileNotFoundError, ValueError) as e:
        raise RuntimeError(
            f"TRENDS_MODE=live ile başlatılamadı: {e} "
            "Cookie olmadan çalıştırmak için TRENDS_MODE=mock kullanın."
        ) from e


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Uygulama açılırken kaynakları hazırlar, kapanırken serbest bırakır."""
    settings: Settings = app.state.settings

    # Dışarıdan client verilmediyse çalışma moduna göre oluştur
    if app.state.trends_client is None:
        app.state.trends_client = build_trends_client(settings)
    # Trends client hazır

    # Veritabanı engine'ini oluştur ve eksik tabloları hazırla
    engine = create_db_engine(settings.DATABASE_URL)
    init_db(engine)
    app.state.engine = engine
    # Veritabanı hazır

    # Önceki çalışmadan yarım kalan işleri toparla ve bekleyen kuyruğu sürdür
    interrupted_count = recover_interrupted_jobs(engine)
    if interrupted_count:
        logger.warning("Yarıda kesilmiş %s job failed olarak işaretlendi", interrupted_count)
    resume_pending_jobs(engine, app.state.trends_client, settings)
    # Kuyruk kurtarma tamamlandı

    yield

    # Kapanışta bağlantıları serbest bırak
    engine.dispose()
    # Kaynaklar serbest bırakıldı


def create_app(settings: Settings | None = None, trends_client: TrendsClient | None = None) -> FastAPI:
    """FastAPI uygulamasını oluşturur.

    Ayar verilmezse `.env`/ortam değişkenleri kullanılır; trends client verilmezse
    TRENDS_MODE'a göre açılışta oluşturulur.
    """
    # Ayarları belirle ve uygulamayı kur
    settings = settings or get_settings()
    app = FastAPI(
        title=settings.APP_NAME,
        description=APP_DESCRIPTION,
        version=__version__,
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.trends_client = trends_client
    # Uygulama nesnesi hazır

    # Parola koruması yalnızca kullanıcı adı ve parola ayarlandıysa devreye girer
    if settings.auth_enabled:
        app.add_middleware(BasicAuthMiddleware, username=settings.AUTH_USERNAME, password=settings.AUTH_PASSWORD)
    # Koruma katmanı hazır

    # Router'ları kaydet
    app.include_router(health.router)
    app.include_router(jobs.router)
    app.include_router(keywords.router)
    app.include_router(insights.router)
    # Router kaydı tamamlandı

    # Web arayüzü: statik dosyalar /ui altında sunulur, kök adres oraya yönlendirir
    app.include_router(ui.router)
    app.mount(ui.UI_PATH, StaticFiles(directory=ui.STATIC_DIR, html=True), name="ui")
    # Web arayüzü hazır

    return app


# `uvicorn app.main:app` için modül seviyesinde uygulama nesnesi
app = create_app()
