# Analiz sorguları: bir ürünün geçmişi, breakout aramalar ve iki tarama arasındaki fark
# Fonksiyonlar HTTP'den bağımsızdır; router yalnızca parametre doğrular ve hata kodlarını üretir

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func
from sqlmodel import Session, select

from app.db.models import JobKeyword, KeywordStatus, QueryResult, QueryType, ScrapeJob
from app.schemas.insights import (
    BreakoutRead,
    DiffChange,
    DiffItem,
    DiffResponse,
    HistoryRead,
    HistoryRisingItem,
    HistoryScan,
    HistoryTopItem,
    KeywordDiff,
    TypeDiff,
)
from app.services.trends.base import normalize_keyword


# --- History ---

def get_keyword_history(
    session: Session, keyword: str, query_type: QueryType | None, limit: int
) -> HistoryRead | None:
    """Bir ürünün son `limit` başarılı taramasını en yeniden eskiye döner; hiç tarama yoksa None."""
    # Keyword'ün başarılı taramalarını job bilgisiyle birlikte getir (yazılış farkı gözetmeden)
    scans = session.exec(
        select(JobKeyword, ScrapeJob)
        .join(ScrapeJob, JobKeyword.job_id == ScrapeJob.id)
        .where(JobKeyword.keyword_normalized == normalize_keyword(keyword))
        .where(JobKeyword.status == KeywordStatus.success)
        .order_by(JobKeyword.processed_at.desc(), JobKeyword.id.desc())
        .limit(limit)
    ).all()
    if not scans:
        return None
    # Taramalar bulundu

    # Bu taramaların tüm sonuçlarını tek sorguyla getir ve taramaya göre grupla
    keyword_ids = [job_keyword.id for job_keyword, _ in scans]
    results_query = select(QueryResult).where(QueryResult.job_keyword_id.in_(keyword_ids)).order_by(QueryResult.rank)
    if query_type is not None:
        results_query = results_query.where(QueryResult.type == query_type)
    results_by_keyword: dict[int, list[QueryResult]] = {keyword_id: [] for keyword_id in keyword_ids}
    for result in session.exec(results_query).all():
        results_by_keyword[result.job_keyword_id].append(result)
    # Sonuçlar gruplandı

    # Her tarama için top ve rising listelerini kur
    history_scans = []
    for job_keyword, job in scans:
        results = results_by_keyword[job_keyword.id]
        history_scans.append(
            HistoryScan(
                job_id=job.id,
                scanned_at=job_keyword.processed_at,
                geo=job.geo,
                timeframe=job.timeframe,
                top=[
                    HistoryTopItem(rank=result.rank, query=result.query, value=result.value)
                    for result in results
                    if result.type == QueryType.top
                ],
                rising=[
                    HistoryRisingItem(
                        rank=result.rank, query=result.query, value=result.value, is_breakout=result.is_breakout
                    )
                    for result in results
                    if result.type == QueryType.rising
                ],
            )
        )
    # Geçmiş hazır

    # Yanıtta keyword'ün en son taramadaki yazılışı gösterilir
    latest_keyword, _ = scans[0]
    return HistoryRead(keyword=latest_keyword.keyword, scans=history_scans)


# --- Breakouts ---

def list_breakouts(
    session: Session,
    keyword: str | None,
    job_id: int | None,
    since: datetime | None,
    limit: int,
    offset: int,
) -> tuple[list[BreakoutRead], int]:
    """Breakout aramaları en yüksek değer önce olacak şekilde döner; (sayfa, toplam sayı)."""
    # Breakout satırlarını seed keyword ve tarama zamanıyla birlikte seç
    query = (
        select(JobKeyword.keyword, QueryResult.query, QueryResult.value, JobKeyword.job_id, JobKeyword.processed_at)
        .join(JobKeyword, QueryResult.job_keyword_id == JobKeyword.id)
        .where(QueryResult.is_breakout.is_(True))
    )
    # Temel sorgu hazır

    # Verilen filtreleri uygula
    if keyword is not None:
        query = query.where(JobKeyword.keyword_normalized == normalize_keyword(keyword))
    if job_id is not None:
        query = query.where(JobKeyword.job_id == job_id)
    if since is not None:
        query = query.where(JobKeyword.processed_at >= since)
    # Filtreler uygulandı

    # Toplam sayıyı ve istenen sayfayı getir (eşit değerlerde yeni tarama önce)
    total = session.exec(select(func.count()).select_from(query.subquery())).one()
    rows = session.exec(
        query.order_by(QueryResult.value.desc(), JobKeyword.processed_at.desc(), QueryResult.id)
        .offset(offset)
        .limit(limit)
    ).all()
    # Sayfa getirildi

    breakouts = [
        BreakoutRead(seed_keyword=seed_keyword, query=query_text, value=value, job_id=row_job_id, scanned_at=scanned_at)
        for seed_keyword, query_text, value, row_job_id, scanned_at in rows
    ]
    return breakouts, total


# --- Diff ---

def diff_results(old_results: Sequence[QueryResult], new_results: Sequence[QueryResult]) -> TypeDiff:
    """Aynı türdeki iki sonuç listesini karşılaştırır: yeni girenler, çıkanlar ve değişenler.

    Sorgular büyük/küçük harf duyarsız eşleştirilir. Saf fonksiyondur; veritabanına dokunmaz.
    """
    # Sorguları küçük harfli hallerine göre indeksle
    old_by_query = {result.query.lower(): result for result in old_results}
    new_by_query = {result.query.lower(): result for result in new_results}
    # İndeksler hazır

    # Yalnızca yeni taramada olanlar (yeni sıraya göre)
    new_items = [
        DiffItem(query=result.query, value=result.value)
        for key, result in sorted(new_by_query.items(), key=lambda item: item[1].rank)
        if key not in old_by_query
    ]
    # Yalnızca eski taramada olanlar (eski sıraya göre)
    dropped_items = [
        DiffItem(query=result.query, value=result.value)
        for key, result in sorted(old_by_query.items(), key=lambda item: item[1].rank)
        if key not in new_by_query
    ]
    # Giren ve çıkanlar belirlendi

    # Her iki taramada olup değeri veya sırası değişenler (yeni sıraya göre)
    changed_items = []
    for key, new_result in sorted(new_by_query.items(), key=lambda item: item[1].rank):
        old_result = old_by_query.get(key)
        if old_result is None:
            continue
        delta = new_result.value - old_result.value
        if delta != 0 or new_result.rank != old_result.rank:
            changed_items.append(
                DiffChange(
                    query=new_result.query,
                    old_value=old_result.value,
                    new_value=new_result.value,
                    delta=delta,
                    old_rank=old_result.rank,
                    new_rank=new_result.rank,
                )
            )
    # Değişenler belirlendi

    return TypeDiff(new=new_items, dropped=dropped_items, changed=changed_items)


def _successful_keywords(job: ScrapeJob, keyword: str | None) -> dict[str, JobKeyword]:
    """Job'ın başarılı keyword'lerini normalize hallerine göre indeksler; keyword verilirse yalnızca onu alır."""
    wanted = normalize_keyword(keyword) if keyword is not None else None
    return {
        job_keyword.keyword_normalized: job_keyword
        for job_keyword in job.keywords
        if job_keyword.status == KeywordStatus.success and wanted in (None, job_keyword.keyword_normalized)
    }


def _results_of_type(job_keyword: JobKeyword, query_type: QueryType) -> list[QueryResult]:
    """Bir keyword'ün verilen türdeki sonuçlarını döner."""
    return [result for result in job_keyword.results if result.type == query_type]


def build_diff(
    from_job: ScrapeJob, to_job: ScrapeJob, keyword: str | None, query_type: QueryType | None
) -> DiffResponse:
    """İki taramayı, her ikisinde de başarılı olan ortak seed keyword'ler üzerinden karşılaştırır."""
    # Parametreleri farklı taramaların karşılaştırması yanıltıcı olabilir: hata değil, uyarı
    warnings = []
    if from_job.geo != to_job.geo:
        warnings.append(f"geo differs: '{from_job.geo}' → '{to_job.geo}'")
    if from_job.timeframe != to_job.timeframe:
        warnings.append(f"timeframe differs: '{from_job.timeframe}' → '{to_job.timeframe}'")
    # Uyarılar belirlendi

    # Her iki taramanın başarılı keyword'lerini eşleştir
    from_keywords = _successful_keywords(from_job, keyword)
    to_keywords = _successful_keywords(to_job, keyword)
    # Keyword'ler eşleştirildi

    # Ortak keyword'leri yeni taramadaki sırayla karşılaştır; istenmeyen tür boş bırakılır
    empty_diff = TypeDiff(new=[], dropped=[], changed=[])
    keyword_diffs = []
    for normalized, to_keyword in to_keywords.items():
        from_keyword = from_keywords.get(normalized)
        if from_keyword is None:
            continue
        type_diffs = {
            current_type: diff_results(
                _results_of_type(from_keyword, current_type), _results_of_type(to_keyword, current_type)
            )
            if query_type in (None, current_type)
            else empty_diff
            for current_type in QueryType
        }
        keyword_diffs.append(
            KeywordDiff(keyword=to_keyword.keyword, top=type_diffs[QueryType.top], rising=type_diffs[QueryType.rising])
        )
    # Ortak keyword'ler karşılaştırıldı

    return DiffResponse(
        from_job=from_job.id,
        to_job=to_job.id,
        warnings=warnings,
        keywords=keyword_diffs,
        only_in_from=[
            job_keyword.keyword for normalized, job_keyword in from_keywords.items() if normalized not in to_keywords
        ],
        only_in_to=[
            job_keyword.keyword for normalized, job_keyword in to_keywords.items() if normalized not in from_keywords
        ],
    )
