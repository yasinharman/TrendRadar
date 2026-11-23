# Ürün (seed keyword) bazlı endpoint'ler: bir ürünün taramalar boyunca geçmişi

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.db.models import QueryType
from app.dependencies import SessionDep, SettingsDep
from app.schemas.common import ErrorResponse
from app.schemas.insights import HistoryRead
from app.services.insights import get_keyword_history

router = APIRouter(prefix="/keywords", tags=["keywords"])

# limit verilmezse dönen tarama sayısı
DEFAULT_HISTORY_LIMIT = 10


@router.get(
    # :path sayesinde içinde "/" geçen ürün adları da (ör. "AC/DC Shirts") kabul edilir
    "/{keyword:path}/history",
    response_model=HistoryRead,
    summary="Ürünün tarama geçmişi",
    description=(
        "Bir ürünün farklı zamanlardaki başarılı taramalarını en yeniden eskiye döner. "
        "Ürün adı büyük/küçük harf ve boşluk farkı gözetmeden eşleştirilir."
    ),
    responses={status.HTTP_404_NOT_FOUND: {"model": ErrorResponse, "description": "Bu ürün hiç başarıyla taranmamış"}},
)
def keyword_history(
    keyword: str,
    session: SessionDep,
    settings: SettingsDep,
    query_type: Annotated[
        QueryType | None, Query(alias="type", description="Yalnızca top veya yalnızca rising sonuçlar.")
    ] = None,
    limit: Annotated[int, Query(ge=1, description="En fazla kaç tarama döneceği (en yeniler).")] = DEFAULT_HISTORY_LIMIT,
) -> HistoryRead:
    """Keyword'ün son taramalarını sonuçlarıyla birlikte döner."""
    # Üst sınır ayarlardan gelir
    if limit > settings.MAX_PAGE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"limit en fazla {settings.MAX_PAGE_SIZE} olabilir",
        )
    # Limit kontrolü tamamlandı

    history = get_keyword_history(session, keyword, query_type, limit)
    if history is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bu keyword için başarılı tarama bulunamadı")
    return history
