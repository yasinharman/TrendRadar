# Ürün (seed keyword) bazlı endpoint'ler: bir ürünün taramalar boyunca geçmişi

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status

from app.db.models import QueryType
from app.dependencies import SessionDep, SettingsDep, validation_error
from app.schemas.common import error_response
from app.schemas.insights import HistoryRead
from app.services.insights import get_keyword_history

router = APIRouter(prefix="/keywords", tags=["keywords"])

# limit verilmezse dönen tarama sayısı
DEFAULT_HISTORY_LIMIT = 10

# Ürün hiç başarıyla taranmamışsa dönen mesaj
NO_HISTORY_MESSAGE = "No successful scan found for this keyword"


@router.get(
    # :path sayesinde içinde "/" geçen ürün adları da (ör. "AC/DC Shirts") kabul edilir
    "/{keyword:path}/history",
    response_model=HistoryRead,
    summary="Ürünün tarama geçmişi",
    description=(
        "Bir ürünün farklı zamanlardaki başarılı taramalarını en yeniden eskiye döner. "
        "Ürün adı büyük/küçük harf ve boşluk farkı gözetmeden eşleştirilir."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: error_response("Bu ürün hiç başarıyla taranmamış", NO_HISTORY_MESSAGE)
    },
)
def keyword_history(
    keyword: Annotated[str, Path(description="Ürün adı (seed keyword).", examples=["Camera Bags"])],
    session: SessionDep,
    settings: SettingsDep,
    query_type: Annotated[
        QueryType | None, Query(alias="type", description="Yalnızca top veya yalnızca rising sonuçlar.")
    ] = None,
    limit: Annotated[
        int, Query(ge=1, description="En fazla kaç tarama döneceği (en yeniler). En fazla MAX_PAGE_SIZE.")
    ] = DEFAULT_HISTORY_LIMIT,
) -> HistoryRead:
    """Keyword'ün son taramalarını sonuçlarıyla birlikte döner."""
    # Üst sınır ayarlardan gelir
    if limit > settings.MAX_PAGE_SIZE:
        raise validation_error("query", "limit", f"limit can be at most {settings.MAX_PAGE_SIZE}")
    # Limit kontrolü tamamlandı

    history = get_keyword_history(session, keyword, query_type, limit)
    if history is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NO_HISTORY_MESSAGE)
    return history
