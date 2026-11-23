# Tarama işi (job) endpoint'leri: oluşturma, listeleme, detay ve silme
# Tarama uzun sürdüğü için istek içinde yapılmaz: iş kuyruğa alınır, 202 dönülür, ilerleme sorgulanır

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Response, status
from sqlalchemy import func
from sqlmodel import Session, select

from app.core.config import Settings
from app.db.models import JobKeyword, JobStatus, QueryResult, QueryType, ScrapeJob
from app.dependencies import EngineDep, PaginationDep, SessionDep, SettingsDep, TrendsClientDep
from app.schemas.common import ErrorResponse, Page
from app.schemas.jobs import JobCreate, JobDetail, JobRead, KeywordSummary
from app.services.job_runner import get_queue_positions, process_queue
from app.services.trends.base import normalize_keyword

router = APIRouter(prefix="/jobs", tags=["jobs"])

# Swagger'da tekrar kullanılan hata yanıtı tanımları
NOT_FOUND_RESPONSE = {status.HTTP_404_NOT_FOUND: {"model": ErrorResponse, "description": "Job bulunamadı"}}


# --- Yardımcılar ---

def get_job_or_404(session: Session, job_id: int) -> ScrapeJob:
    """Job'ı getirir; yoksa 404 fırlatır."""
    job = session.get(ScrapeJob, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job bulunamadı")
    return job


def check_keyword_limits(keywords: list[str], settings: Settings) -> None:
    """Keyword sayısını ve uzunluğunu ayarlardaki sınırlarla karşılaştırır; aşılırsa 422 fırlatır."""
    # Adet sınırı (tekrarlar temizlendikten sonraki sayıya uygulanır)
    if len(keywords) > settings.MAX_KEYWORDS_PER_JOB:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Bir taramada en fazla {settings.MAX_KEYWORDS_PER_JOB} keyword gönderilebilir",
        )
    # Adet kontrolü tamamlandı

    # Uzunluk sınırı
    for keyword in keywords:
        if len(keyword) > settings.MAX_KEYWORD_LENGTH:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Keyword en fazla {settings.MAX_KEYWORD_LENGTH} karakter olabilir",
            )
    # Uzunluk kontrolü tamamlandı


def to_job_read(job: ScrapeJob, queue_positions: dict[int, int]) -> JobRead:
    """Veritabanı kaydını yanıt şemasına çevirir; ilerleme metnini ve kuyruk sırasını ekler."""
    return JobRead(
        id=job.id,
        status=job.status,
        geo=job.geo,
        timeframe=job.timeframe,
        total_keywords=job.total_keywords,
        processed_keywords=job.processed_keywords,
        progress=f"{job.processed_keywords}/{job.total_keywords}",
        queue_position=queue_positions.get(job.id),
        error_message=job.error_message,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


def count_results_by_keyword(session: Session, job_id: int) -> dict[tuple[int, QueryType], int]:
    """Job'ın her keyword'ü için top ve rising sonuç sayılarını tek sorguyla getirir."""
    rows = session.exec(
        select(QueryResult.job_keyword_id, QueryResult.type, func.count())
        .join(JobKeyword)
        .where(JobKeyword.job_id == job_id)
        .group_by(QueryResult.job_keyword_id, QueryResult.type)
    ).all()
    return {(keyword_id, query_type): count for keyword_id, query_type, count in rows}


# --- Endpoint'ler ---

@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobRead,
    summary="Yeni tarama başlat",
    description=(
        "Verilen ürün adları için bir tarama işi oluşturur ve kuyruğa ekler. Tarama arka planda yürür; "
        "yanıt hemen döner. İlerlemeyi `Location` header'ındaki adresten sorgulayın.\n\n"
        'Mock modda içinde **"fail"** geçen keyword hata, **"empty"** geçen keyword boş veri senaryosunu tetikler.'
    ),
    responses={
        status.HTTP_202_ACCEPTED: {
            "description": "İş kuyruğa alındı",
            "headers": {"Location": {"description": "Oluşturulan işin adresi", "schema": {"type": "string"}}},
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Geçersiz keyword listesi, ülke kodu veya zaman aralığı"},
    },
)
def create_job(
    payload: JobCreate,
    response: Response,
    background_tasks: BackgroundTasks,
    session: SessionDep,
    settings: SettingsDep,
    engine: EngineDep,
    trends_client: TrendsClientDep,
) -> JobRead:
    """Job ve keyword kayıtlarını oluşturur, worker'ı arka planda tetikler."""
    # Ayarlara bağlı sınırları kontrol et ve verilmeyen parametreler için varsayılanları uygula
    check_keyword_limits(payload.keywords, settings)
    geo = settings.DEFAULT_GEO if payload.geo is None else payload.geo
    timeframe = payload.timeframe or settings.DEFAULT_TIMEFRAME
    # İstek doğrulandı

    # Job'ı ve keyword'lerini istekteki sırayla kaydet
    job = ScrapeJob(
        geo=geo,
        timeframe=timeframe,
        total_keywords=len(payload.keywords),
        keywords=[
            JobKeyword(keyword=keyword, keyword_normalized=normalize_keyword(keyword), position=position)
            for position, keyword in enumerate(payload.keywords)
        ],
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    # Job kaydedildi

    # Yanıt gönderildikten sonra kuyruğu işlet; zaten çalışan worker varsa bu çağrı hemen döner
    background_tasks.add_task(process_queue, engine, trends_client, settings)
    # Worker tetiklendi

    response.headers["Location"] = f"/jobs/{job.id}"
    return to_job_read(job, get_queue_positions(session))


@router.get(
    "",
    response_model=Page[JobRead],
    summary="Tarama işlerini listele",
    description="İşleri en yeniden eskiye sıralı döner. `status` ile filtrelenebilir.",
)
def list_jobs(
    session: SessionDep,
    pagination: PaginationDep,
    status_filter: Annotated[
        JobStatus | None, Query(alias="status", description="Yalnızca bu durumdaki işleri getir.")
    ] = None,
) -> Page[JobRead]:
    """Sayfalanmış job listesini döner."""
    # Filtreyi hem sayım hem liste sorgusuna uygula
    query = select(ScrapeJob)
    count_query = select(func.count()).select_from(ScrapeJob)
    if status_filter is not None:
        query = query.where(ScrapeJob.status == status_filter)
        count_query = count_query.where(ScrapeJob.status == status_filter)
    # Sorgular hazır

    # Toplam sayıyı ve istenen sayfayı getir (en yeni önce)
    total = session.exec(count_query).one()
    jobs = session.exec(
        query.order_by(ScrapeJob.id.desc()).offset(pagination.offset).limit(pagination.limit)
    ).all()
    # Sayfa getirildi

    queue_positions = get_queue_positions(session)
    return Page[JobRead](
        items=[to_job_read(job, queue_positions) for job in jobs],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get(
    "/{job_id}",
    response_model=JobDetail,
    summary="İş detayı ve ilerlemesi",
    description="İşin durumunu, ilerlemesini ve her keyword için sonuç sayılarını veya hata nedenini döner.",
    responses=NOT_FOUND_RESPONSE,
)
def get_job(job_id: int, session: SessionDep) -> JobDetail:
    """Job bilgilerini keyword bazında özetle birlikte döner."""
    job = get_job_or_404(session, job_id)

    # Her keyword için top/rising sonuç sayılarını ekle
    counts = count_results_by_keyword(session, job.id)
    keywords = [
        KeywordSummary(
            keyword=job_keyword.keyword,
            status=job_keyword.status,
            top_count=counts.get((job_keyword.id, QueryType.top), 0),
            rising_count=counts.get((job_keyword.id, QueryType.rising), 0),
            error_message=job_keyword.error_message,
        )
        for job_keyword in job.keywords
    ]
    # Keyword özeti hazır

    job_read = to_job_read(job, get_queue_positions(session))
    return JobDetail(**job_read.model_dump(), keywords=keywords)


@router.delete(
    "/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="İşi sil",
    description=(
        "İşi, keyword'lerini ve sonuçlarını siler. Bekleyen iş silinirse kuyruktan çıkar. "
        "Çalışan iş silinemez."
    ),
    responses={
        **NOT_FOUND_RESPONSE,
        status.HTTP_409_CONFLICT: {"model": ErrorResponse, "description": "İş şu anda çalışıyor"},
    },
)
def delete_job(job_id: int, session: SessionDep) -> None:
    """Çalışmayan bir job'ı tüm bağlı kayıtlarıyla siler."""
    job = get_job_or_404(session, job_id)

    # Çalışan job silinirse worker'ın yazdığı kayıtlar sahipsiz kalır
    if job.status == JobStatus.running:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Çalışan job silinemez")
    # Durum kontrolü tamamlandı

    session.delete(job)
    session.commit()
