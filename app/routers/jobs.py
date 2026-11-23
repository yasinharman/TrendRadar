# Tarama işi (job) endpoint'leri: oluşturma, listeleme, detay ve silme
# Tarama uzun sürdüğü için istek içinde yapılmaz: iş kuyruğa alınır, 202 dönülür, ilerleme sorgulanır

import csv
import io
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, HTTPException, Path, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import case, func
from sqlmodel import Session, select

from app.core.config import Settings
from app.db.models import JobKeyword, JobStatus, QueryResult, QueryType, ScrapeJob
from app.dependencies import (
    EngineDep,
    PaginationDep,
    SessionDep,
    SettingsDep,
    TrendsClientDep,
    validation_error,
)
from app.schemas.common import Page, error_response
from app.schemas.jobs import JobCreate, JobDetail, JobRead, KeywordSummary
from app.schemas.results import QueryResultRead, ResultFilters
from app.services.job_runner import get_queue_positions, process_queue
from app.services.trends.base import normalize_keyword

router = APIRouter(prefix="/jobs", tags=["jobs"])

# Yol parametresi: tüm /jobs/{job_id} endpoint'lerinde aynı açıklamayla kullanılır
JobIdPath = Annotated[int, Path(description="İşin id'si (POST /jobs yanıtındaki `id`).")]

# Hata mesajları
JOB_NOT_FOUND_MESSAGE = "Job bulunamadı"
RUNNING_JOB_DELETE_MESSAGE = "Çalışan job silinemez"

# Swagger'da tekrar kullanılan hata yanıtı tanımı
NOT_FOUND_RESPONSE = {status.HTTP_404_NOT_FOUND: error_response("Job bulunamadı", JOB_NOT_FOUND_MESSAGE)}

# Sonuç sıralamasında türlerin sırası: önce top, sonra rising
TOP_SORT_ORDER = 0
RISING_SORT_ORDER = 1

# CSV export ayarları
CSV_MEDIA_TYPE = "text/csv"
CSV_COLUMNS = ["seed_keyword", "type", "rank", "query", "value", "is_breakout"]
CSV_BOM = "\ufeff"                       # Excel'in UTF-8'i tanıması için (eski scraper'daki utf-8-sig davranışı)
CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
CSV_ESCAPE_CHAR = "'"

# Taraması bitmiş ve en az bir keyword'ü başarılı olan işlerin durumları
# (CSV export ve diff yalnızca bu işler için yapılabilir)
SUCCESSFUL_STATUSES = (JobStatus.completed, JobStatus.partial)


# --- Yardımcılar ---

def get_job_or_404(session: Session, job_id: int) -> ScrapeJob:
    """Job'ı getirir; yoksa 404 fırlatır."""
    job = session.get(ScrapeJob, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=JOB_NOT_FOUND_MESSAGE)
    return job


def check_keyword_limits(keywords: list[str], settings: Settings) -> None:
    """Keyword sayısını ve uzunluğunu ayarlardaki sınırlarla karşılaştırır; aşılırsa 422 fırlatır."""
    # Adet sınırı (tekrarlar temizlendikten sonraki sayıya uygulanır)
    if len(keywords) > settings.MAX_KEYWORDS_PER_JOB:
        raise validation_error(
            "body", "keywords", f"Bir taramada en fazla {settings.MAX_KEYWORDS_PER_JOB} keyword gönderilebilir"
        )
    # Adet kontrolü tamamlandı

    # Uzunluk sınırı
    for keyword in keywords:
        if len(keyword) > settings.MAX_KEYWORD_LENGTH:
            raise validation_error(
                "body", "keywords", f"Keyword en fazla {settings.MAX_KEYWORD_LENGTH} karakter olabilir"
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


def build_results_query(job_id: int, filters: ResultFilters):
    """Bir job'ın sonuçlarını seed keyword'üyle birlikte seçen, filtrelenmiş ve sıralanmış sorguyu kurar."""
    # Her sonuç satırı, ait olduğu seed keyword'ün yazılışıyla birlikte seçilir
    query = (
        select(JobKeyword.keyword, QueryResult)
        .join(JobKeyword, QueryResult.job_keyword_id == JobKeyword.id)
        .where(JobKeyword.job_id == job_id)
    )
    # Temel sorgu hazır

    # Verilen filtreleri uygula
    if filters.type is not None:
        query = query.where(QueryResult.type == filters.type)
    if filters.keyword is not None:
        query = query.where(JobKeyword.keyword_normalized == normalize_keyword(filters.keyword))
    if filters.min_value is not None:
        query = query.where(QueryResult.value >= filters.min_value)
    if filters.breakout_only:
        query = query.where(QueryResult.is_breakout.is_(True))
    if filters.search:
        # autoescape: aranan metindeki % ve _ karakterleri joker sayılmaz
        query = query.where(func.lower(QueryResult.query).contains(filters.search.lower(), autoescape=True))
    # Filtreler uygulandı

    # Sıralama: istekteki keyword sırası, önce top sonra rising, sonra Google'ın sırası
    # (type kolonu metin olarak sıralanırsa "rising" öne geçer; bu yüzden açık sıra verilir)
    type_order = case((QueryResult.type == QueryType.top, TOP_SORT_ORDER), else_=RISING_SORT_ORDER)
    return query.order_by(JobKeyword.position, type_order, QueryResult.rank)


def to_result_read(seed_keyword: str, result: QueryResult) -> QueryResultRead:
    """Sonuç satırını seed keyword'üyle birlikte yanıt şemasına çevirir."""
    return QueryResultRead(
        seed_keyword=seed_keyword,
        type=result.type,
        rank=result.rank,
        query=result.query,
        value=result.value,
        is_breakout=result.is_breakout,
    )


def csv_safe(text: str) -> str:
    """Excel'in formül olarak çalıştırabileceği hücreleri etkisizleştirir (CSV injection önlemi).

    Sorgu metinleri dışarıdan (Google'dan ve kullanıcıdan) gelir; "=", "+", "-" veya "@" ile
    başlayan bir hücrenin başına tek tırnak konur, böylece Excel onu düz metin olarak gösterir.
    """
    if text.startswith(CSV_FORMULA_PREFIXES):
        return CSV_ESCAPE_CHAR + text
    return text


def generate_csv(rows: list[QueryResultRead]) -> Iterator[str]:
    """Sonuçları başlık satırıyla birlikte satır satır CSV metni olarak üretir."""
    # Her satır aynı tampona yazılıp gönderilir, sonra tampon boşaltılır
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    def flush() -> str:
        """Tampondaki metni döner ve tamponu boşaltır."""
        text = buffer.getvalue()
        buffer.seek(0)
        buffer.truncate()
        return text
    # CSV yazıcısı hazır

    # BOM ve başlık satırı
    writer.writerow(CSV_COLUMNS)
    yield CSV_BOM + flush()
    # Başlık gönderildi

    # Veri satırları
    for row in rows:
        writer.writerow(
            [
                csv_safe(row.seed_keyword),
                row.type.value,
                row.rank,
                csv_safe(row.query),
                row.value,
                str(row.is_breakout).lower(),
            ]
        )
        yield flush()
    # Tüm satırlar gönderildi


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
def get_job(job_id: JobIdPath, session: SessionDep) -> JobDetail:
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
        status.HTTP_409_CONFLICT: error_response("İş şu anda çalışıyor", RUNNING_JOB_DELETE_MESSAGE),
    },
)
def delete_job(job_id: JobIdPath, session: SessionDep) -> None:
    """Çalışmayan bir job'ı tüm bağlı kayıtlarıyla siler."""
    job = get_job_or_404(session, job_id)

    # Çalışan job silinirse worker'ın yazdığı kayıtlar sahipsiz kalır
    if job.status == JobStatus.running:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=RUNNING_JOB_DELETE_MESSAGE)
    # Durum kontrolü tamamlandı

    session.delete(job)
    session.commit()


@router.get(
    "/{job_id}/results",
    response_model=Page[QueryResultRead],
    summary="İşin sonuçlarını getir",
    description=(
        "İşin bulduğu top ve rising aramaları filtreleyerek döner. Sıralama: istekteki keyword sırası, "
        "önce top sonra rising, sonra Google'ın sırası.\n\n"
        "İş henüz bitmediyse o ana kadar biriken sonuçlar döner (hata değildir)."
    ),
    responses=NOT_FOUND_RESPONSE,
)
def list_results(
    job_id: JobIdPath,
    session: SessionDep,
    pagination: PaginationDep,
    filters: Annotated[ResultFilters, Query()],
) -> Page[QueryResultRead]:
    """Filtrelenmiş ve sayfalanmış sonuç listesini döner."""
    job = get_job_or_404(session, job_id)

    # Toplam sayıyı ve istenen sayfayı aynı filtrelerle getir
    query = build_results_query(job.id, filters)
    total = session.exec(select(func.count()).select_from(query.order_by(None).subquery())).one()
    rows = session.exec(query.offset(pagination.offset).limit(pagination.limit)).all()
    # Sayfa getirildi

    return Page[QueryResultRead](
        items=[to_result_read(seed_keyword, result) for seed_keyword, result in rows],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get(
    "/{job_id}/export.csv",
    response_class=StreamingResponse,
    summary="Sonuçları CSV olarak indir",
    description=(
        "İşin tüm sonuçlarını Excel uyumlu (UTF-8 BOM'lu) CSV dosyası olarak indirir. "
        "`type` ile yalnızca top veya yalnızca rising sonuçlar alınabilir.\n\n"
        "Kolonlar: `seed_keyword, type, rank, query, value, is_breakout`"
    ),
    responses={
        status.HTTP_200_OK: {
            "description": "CSV dosyası",
            "content": {
                CSV_MEDIA_TYPE: {
                    "example": (
                        "seed_keyword,type,rank,query,value,is_breakout\r\n"
                        "Camera Bags,top,1,camera bag,100,false\r\n"
                        "Camera Bags,rising,1,mindshift camera bags,800,false\r\n"
                    )
                }
            },
        },
        **NOT_FOUND_RESPONSE,
        status.HTTP_409_CONFLICT: error_response(
            "İş henüz tamamlanmadı veya başarısız oldu",
            "Job 'running' durumunda; CSV yalnızca completed veya partial işler için indirilebilir",
        ),
    },
)
def export_results_csv(
    job_id: JobIdPath,
    session: SessionDep,
    result_type: Annotated[
        QueryType | None, Query(alias="type", description="Yalnızca top veya yalnızca rising sonuçlar.")
    ] = None,
) -> StreamingResponse:
    """Tamamlanmış bir job'ın sonuçlarını CSV olarak akıtır."""
    job = get_job_or_404(session, job_id)

    # Yarım veya başarısız taramanın dosyası yanıltıcı olur; yalnızca bitmiş işler indirilebilir
    if job.status not in SUCCESSFUL_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job '{job.status.value}' durumunda; CSV yalnızca completed veya partial işler için indirilebilir",
        )
    # Durum kontrolü tamamlandı

    # Satırlar, veritabanı oturumu kapanmadan önce belleğe alınır (bir job en fazla birkaç yüz satırdır)
    query = build_results_query(job.id, ResultFilters(type=result_type))
    rows = [to_result_read(seed_keyword, result) for seed_keyword, result in session.exec(query).all()]
    # Satırlar hazır

    return StreamingResponse(
        generate_csv(rows),
        media_type=CSV_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="trendradar_job_{job.id}.csv"'},
    )
