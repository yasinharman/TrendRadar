# Analiz endpoint'leri: tüm taramalardaki breakout aramalar ve iki tarama arasındaki fark

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.db.models import QueryType
from app.dependencies import PaginationDep, SessionDep, validation_error
from app.routers.jobs import JOB_NOT_FOUND_MESSAGE, SUCCESSFUL_STATUSES, get_job_or_404
from app.schemas.common import Page, error_response
from app.schemas.insights import BreakoutRead, DiffResponse
from app.services.insights import build_diff, list_breakouts

router = APIRouter(prefix="/insights", tags=["insights"])


@router.get(
    "/breakouts",
    response_model=Page[BreakoutRead],
    summary="Breakout aramalar",
    description=(
        "Tüm taramalardaki breakout (yükselişi eşiğin üzerinde) aramaları en yüksek değer önce olacak şekilde döner. "
        "Ürüne, işe veya tarihe göre daraltılabilir."
    ),
)
def breakouts(
    session: SessionDep,
    pagination: PaginationDep,
    keyword: Annotated[
        str | None,
        Query(
            description="Yalnızca bu seed keyword'ün breakout'ları (büyük/küçük harf duyarsız).",
            examples=["Camera Bags"],
        ),
    ] = None,
    job_id: Annotated[int | None, Query(description="Yalnızca bu işin breakout'ları.")] = None,
    since: Annotated[
        datetime | None,
        Query(
            description="Bu zamandan sonra taranmış olanlar (ISO 8601; saat dilimi verilmezse UTC kabul edilir).",
            examples=["2026-11-01T00:00:00Z"],
        ),
    ] = None,
) -> Page[BreakoutRead]:
    """Filtrelenmiş ve sayfalanmış breakout listesini döner."""
    items, total = list_breakouts(session, keyword, job_id, since, pagination.limit, pagination.offset)
    return Page[BreakoutRead](items=items, total=total, limit=pagination.limit, offset=pagination.offset)


@router.get(
    "/diff",
    response_model=DiffResponse,
    summary="İki taramayı karşılaştır",
    description=(
        "İki tarama arasında, **her ikisinde de başarılı olan** ortak ürünler için listeye yeni giren, "
        "listeden çıkan ve değeri/sırası değişen aramaları döner. Aramalar büyük/küçük harf duyarsız eşleştirilir.\n\n"
        "`geo` veya `timeframe` farklıysa hata verilmez; `warnings` alanı doldurulur. "
        "`from_job` ile `to_job` aynı olamaz (422)."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: error_response("İşlerden biri bulunamadı", JOB_NOT_FOUND_MESSAGE),
        status.HTTP_409_CONFLICT: error_response(
            "İşlerden biri henüz tamamlanmadı veya başarısız oldu",
            "Job 7 'running' durumunda; yalnızca completed veya partial işler karşılaştırılabilir",
        ),
    },
)
def diff(
    session: SessionDep,
    from_job: Annotated[int, Query(description="Eski taramanın id'si.")],
    to_job: Annotated[int, Query(description="Yeni taramanın id'si.")],
    keyword: Annotated[
        str | None,
        Query(
            description="Yalnızca bu seed keyword karşılaştırılır (büyük/küçük harf duyarsız).",
            examples=["Camera Bags"],
        ),
    ] = None,
    query_type: Annotated[
        QueryType | None,
        Query(alias="type", description="Yalnızca top veya yalnızca rising karşılaştırılır; diğeri boş döner."),
    ] = None,
) -> DiffResponse:
    """İki job'ı doğrular ve karşılaştırma sonucunu döner."""
    # Bir taramanın kendisiyle karşılaştırılması anlamsızdır
    if from_job == to_job:
        raise validation_error("query", "to_job", "from_job ve to_job farklı olmalı")
    # Parametre kontrolü tamamlandı

    # Her iki job da var olmalı ve sonuç üretmiş olmalı
    old_job = get_job_or_404(session, from_job)
    new_job = get_job_or_404(session, to_job)
    for job in (old_job, new_job):
        if job.status not in SUCCESSFUL_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Job {job.id} '{job.status.value}' durumunda; "
                "yalnızca completed veya partial işler karşılaştırılabilir",
            )
    # Job'lar doğrulandı

    return build_diff(old_job, new_job, keyword, query_type)
