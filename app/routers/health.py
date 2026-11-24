# Servis durumu endpoint'i

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError

from app.db.models import JobStatus
from app.dependencies import SessionDep, SettingsDep
from app.schemas.common import HealthResponse, QueueInfo, error_response
from app.services.job_runner import count_jobs

router = APIRouter(tags=["health"])

# Sağlıklı durumda dönen sabit metin
STATUS_OK = "ok"

# Veritabanı yanıt vermediğinde dönen mesaj
DATABASE_DOWN_MESSAGE = "Database is unreachable"


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Servis durumu",
    description="Servisin ayakta olduğunu, çalışma modunu, veritabanı bağlantısını ve kuyruk durumunu gösterir.",
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: error_response("Veritabanına ulaşılamıyor", DATABASE_DOWN_MESSAGE)
    },
)
def health(session: SessionDep, settings: SettingsDep) -> HealthResponse:
    """Kuyruk durumunu veritabanından okur ve servis bilgilerini döner."""
    # Kuyruk sorgusu aynı zamanda veritabanı bağlantısını doğrular
    try:
        queue = QueueInfo(
            pending=count_jobs(session, JobStatus.pending),
            running=count_jobs(session, JobStatus.running),
        )
    except SQLAlchemyError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=DATABASE_DOWN_MESSAGE)
    # Veritabanı kontrolü tamamlandı

    # Servis bilgilerini derle
    return HealthResponse(
        status=STATUS_OK,
        app=settings.APP_NAME,
        mode=settings.TRENDS_MODE,
        database=session.get_bind().dialect.name,
        queue=queue,
    )
