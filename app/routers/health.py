# Servis durumu endpoint'i

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import select

from app.dependencies import SessionDep, SettingsDep
from app.schemas.common import ErrorResponse, HealthResponse

router = APIRouter(tags=["health"])

# Sağlıklı durumda dönen sabit metin
STATUS_OK = "ok"


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Servis durumu",
    description="Servisin ayakta olduğunu, çalışma modunu ve veritabanı bağlantısını doğrular.",
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": "Veritabanına ulaşılamıyor",
        }
    },
)
def health(session: SessionDep, settings: SettingsDep) -> HealthResponse:
    """Veritabanına basit bir sorgu atar ve servis bilgilerini döner."""
    # Bağlantının gerçekten çalıştığını doğrula
    try:
        session.exec(select(1))
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Veritabanına ulaşılamıyor",
        )
    # Veritabanı kontrolü tamamlandı

    # Servis bilgilerini derle
    return HealthResponse(
        status=STATUS_OK,
        app=settings.APP_NAME,
        mode=settings.TRENDS_MODE,
        database=session.get_bind().dialect.name,
    )
