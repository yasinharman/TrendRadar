# Tarama kuyruğu ve job yürütme
# Aynı anda tek tarama çalışır: Google tek cookie oturumundan gelen paralel isteklere 429 döner

import logging
import threading

from sqlalchemy import Engine, func
from sqlmodel import Session, select

from app.core.config import Settings
from app.db.models import (
    JobKeyword,
    JobStatus,
    KeywordStatus,
    QueryResult,
    QueryType,
    ScrapeJob,
    utc_now,
)
from app.services.trends.base import KeywordTrends, TrendsClient, TrendsError, is_breakout

logger = logging.getLogger(__name__)

# Aynı anda yalnızca bir worker'ın çalışmasını sağlayan kilit
_worker_lock = threading.Lock()

# Sunucu yarıda kapanınca çalışır durumda kalan job'lara yazılan mesaj
INTERRUPTED_MESSAGE = "Sunucu yeniden başlatıldığı için yarıda kesildi"

# Google'ın döndürdüğü sıra 1'den başlar
FIRST_RANK = 1


# --- Kuyruk sorguları ---

def count_jobs(session: Session, status: JobStatus) -> int:
    """Verilen durumdaki job sayısını döner."""
    return session.exec(select(func.count()).select_from(ScrapeJob).where(ScrapeJob.status == status)).one()


def get_queue_positions(session: Session) -> dict[int, int]:
    """Bekleyen her job için kendinden önce bekleyen + çalışan job sayısını döner (0 = sıradaki)."""
    running_count = count_jobs(session, JobStatus.running)
    pending_ids = session.exec(
        select(ScrapeJob.id).where(ScrapeJob.status == JobStatus.pending).order_by(ScrapeJob.id)
    ).all()
    return {job_id: index + running_count for index, job_id in enumerate(pending_ids)}


def _next_pending_job(session: Session) -> ScrapeJob | None:
    """Kuyruktaki en eski bekleyen job'ı döner; yoksa None."""
    return session.exec(
        select(ScrapeJob).where(ScrapeJob.status == JobStatus.pending).order_by(ScrapeJob.id)
    ).first()


# --- Kuyruk işleme (tek worker) ---

def process_queue(engine: Engine, trends_client: TrendsClient, settings: Settings) -> None:
    """Bekleyen job'ları en eskiden başlayarak sırayla çalıştırır.

    Her POST /jobs isteği bu fonksiyonu arka planda çağırır; kilit sayesinde yalnızca
    biri worker olur, diğerleri hemen döner.
    """
    # Zaten çalışan worker varsa o, yeni eklenen job'ı da alacaktır
    if not _worker_lock.acquire(blocking=False):
        return
    # Kilit alındı; bu çağrı worker oldu

    try:
        # Kuyruk boşalana kadar job'ları tek tek çalıştır
        while True:
            # Arka plan işi kendi oturumunu açar (istek oturumu çoktan kapanmıştır)
            with Session(engine) as session:
                job = _next_pending_job(session)
                if job is None:
                    break
                run_job(session, job, trends_client, settings)
        # Kuyruk boşaldı
    finally:
        _worker_lock.release()

    # Yarış durumu: kuyruk boş görülüp kilit bırakılana kadar yeni job gelmiş olabilir
    # ve onun çağrısı kilidi alamadan dönmüş olabilir; bu yüzden bir kez daha bakılır
    with Session(engine) as session:
        has_pending = _next_pending_job(session) is not None
    if has_pending:
        process_queue(engine, trends_client, settings)
    # Yarış kontrolü tamamlandı


# --- Tek job'ın yürütülmesi ---

def run_job(session: Session, job: ScrapeJob, trends_client: TrendsClient, settings: Settings) -> None:
    """Bir job'ın tüm keyword'lerini sırayla tarar ve sonuçları veritabanına yazar."""
    job_id = job.id
    try:
        # Job'ı çalışıyor olarak işaretle
        job.status = JobStatus.running
        job.started_at = utc_now()
        session.commit()
        # Başlangıç durumu kaydedildi

        # Her keyword için tam akışı uygula (istekteki sırayla)
        keywords = list(job.keywords)
        for index, job_keyword in enumerate(keywords):
            try:
                trends = trends_client.fetch_keyword(job_keyword.keyword, job.geo, job.timeframe)
                _save_results(session, job_keyword, trends, settings.BREAKOUT_THRESHOLD)
                job_keyword.status = KeywordStatus.success
            except TrendsError as e:
                # Başarısız keyword job'ı durdurmaz; nedeni kaydedilir
                job_keyword.status = KeywordStatus.failed
                job_keyword.error_message = f"{type(e).__name__}: {e}"
            # Tek keyword işlemi bitti

            # İlerleme anlık görünsün diye her keyword sonrası commit
            job_keyword.processed_at = utc_now()
            job.processed_keywords += 1
            session.commit()
            # İlerleme kaydedildi

            # İstekler arası bekleme (son keyword'de atla)
            if index < len(keywords) - 1:
                trends_client.pause_between_keywords()
            # Bekleme adımı tamamlandı

        # Keyword sonuçlarına göre final durumu belirle
        job.status = _final_status(keywords)
        job.finished_at = utc_now()
        session.commit()
        # Job tamamlandı

    except Exception as e:
        # Beklenmeyen hata: job failed olur, worker bir sonraki job'a geçer
        logger.exception("Job %s beklenmeyen hatayla durdu", job_id)
        session.rollback()
        failed_job = session.get(ScrapeJob, job_id)
        if failed_job is None:
            return
        failed_job.status = JobStatus.failed
        failed_job.error_message = f"{type(e).__name__}: {e}"
        failed_job.finished_at = utc_now()
        session.commit()
        # Hata durumu kaydedildi


def _save_results(session: Session, job_keyword: JobKeyword, trends: KeywordTrends, breakout_threshold: int) -> None:
    """Bir keyword'ün top ve rising aramalarını QueryResult satırları olarak ekler."""
    # Top aramalar: değer 0–100 ilgi skoru, breakout kavramı yok
    for rank, item in enumerate(trends.top, start=FIRST_RANK):
        session.add(
            QueryResult(
                job_keyword_id=job_keyword.id, type=QueryType.top, rank=rank, query=item.query, value=item.value
            )
        )
    # Top satırları eklendi

    # Rising aramalar: değer yüzde artış; eşik ve üzeri breakout sayılır
    for rank, item in enumerate(trends.rising, start=FIRST_RANK):
        session.add(
            QueryResult(
                job_keyword_id=job_keyword.id,
                type=QueryType.rising,
                rank=rank,
                query=item.query,
                value=item.value,
                is_breakout=is_breakout(item.value, breakout_threshold),
            )
        )
    # Rising satırları eklendi


def _final_status(keywords: list[JobKeyword]) -> JobStatus:
    """Keyword durumlarına göre job'ın final durumunu belirler."""
    success_count = sum(1 for keyword in keywords if keyword.status == KeywordStatus.success)
    # Hiç başarılı yoksa failed, hepsi başarılıysa completed, karışıksa partial
    if success_count == 0:
        return JobStatus.failed
    if success_count == len(keywords):
        return JobStatus.completed
    return JobStatus.partial


# --- Startup kurtarma ---

def recover_interrupted_jobs(engine: Engine) -> int:
    """Sunucu yarıda kapandığı için çalışır durumda kalmış job'ları failed yapar; kaç job düzeltildiğini döner."""
    with Session(engine) as session:
        interrupted = session.exec(select(ScrapeJob).where(ScrapeJob.status == JobStatus.running)).all()
        for job in interrupted:
            job.status = JobStatus.failed
            job.error_message = INTERRUPTED_MESSAGE
            job.finished_at = utc_now()
        session.commit()
        return len(interrupted)


def resume_pending_jobs(engine: Engine, trends_client: TrendsClient, settings: Settings) -> threading.Thread | None:
    """Kuyrukta bekleyen job varsa worker'ı ayrı bir thread'de başlatır; başlatılan thread'i döner."""
    # Bekleyen job yoksa yapılacak iş yok
    with Session(engine) as session:
        if _next_pending_job(session) is None:
            return None
    # Bekleyen job var

    # Açılışı bekletmemek için worker arka planda çalışır
    worker = threading.Thread(target=process_queue, args=(engine, trends_client, settings), daemon=True)
    worker.start()
    return worker
