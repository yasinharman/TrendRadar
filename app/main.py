# TrendRadar API — uygulama giriş noktası
# create_app() factory'si ayarları dışarıdan alabilir; böylece testler kendi ayarlarını enjekte eder

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.core.config import Settings, get_settings
from app.db.session import create_db_engine, init_db
from app.routers import health

# Swagger arayüzünde gösterilen açıklama
APP_DESCRIPTION = (
    "Ürün isimleri için Google Trends'ten en çok aranan (top) ve yükselişteki (rising) "
    "ilişkili aramaları toplayan, saklayan ve analiz eden REST API."
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Uygulama açılırken kaynakları hazırlar, kapanırken serbest bırakır."""
    # Veritabanı engine'ini oluştur ve eksik tabloları hazırla
    settings: Settings = app.state.settings
    engine = create_db_engine(settings.DATABASE_URL)
    init_db(engine)
    app.state.engine = engine
    # Veritabanı hazır

    yield

    # Kapanışta bağlantıları serbest bırak
    engine.dispose()
    # Kaynaklar serbest bırakıldı


def create_app(settings: Settings | None = None) -> FastAPI:
    """FastAPI uygulamasını oluşturur; ayar verilmezse `.env`/ortam değişkenleri kullanılır."""
    # Ayarları belirle ve uygulamayı kur
    settings = settings or get_settings()
    app = FastAPI(
        title=settings.APP_NAME,
        description=APP_DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = settings
    # Uygulama nesnesi hazır

    # Router'ları kaydet
    app.include_router(health.router)
    # Router kaydı tamamlandı

    return app


# `uvicorn app.main:app` için modül seviyesinde uygulama nesnesi
app = create_app()
