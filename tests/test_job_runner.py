# Job runner testleri: durum kuralları, ilerleme, sıralı işleme, kilit ve startup kurtarma
# Runner doğrudan çağrılır; worker ile gözlemci ayrı bağlantı kullanabilsin diye dosya tabanlı SQLite kullanılır

import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import Settings
from app.db.models import JobKeyword, JobStatus, KeywordStatus, QueryResult, QueryType, ScrapeJob
from app.db.session import create_db_engine, init_db
from app.main import create_app
from app.services import job_runner
from app.services.job_runner import INTERRUPTED_MESSAGE, process_queue, recover_interrupted_jobs
from app.services.trends.base import KeywordTrends, TrendItem
from app.services.trends.mock_client import MockTrendsClient

# Arka plan thread'inin bitmesi için beklenen en uzun süre (saniye)
THREAD_TIMEOUT = 5


# --- Fixture'lar ve yardımcılar ---

@pytest.fixture
def file_settings(tmp_path) -> Settings:
    """Geçici bir SQLite dosyası kullanan beklemesiz mock ayarları."""
    return Settings(
        _env_file=None, TRENDS_MODE="mock", DATABASE_URL=f"sqlite:///{tmp_path / 'runner.db'}", MOCK_DELAY_SECONDS=0
    )


@pytest.fixture
def file_engine(file_settings):
    """Tabloları hazır, dosya tabanlı engine."""
    engine = create_db_engine(file_settings.DATABASE_URL)
    init_db(engine)
    yield engine
    engine.dispose()


def add_job(engine, keywords: list[str], job_status: JobStatus = JobStatus.pending) -> int:
    """Veritabanına bir job yazar ve id'sini döner."""
    with Session(engine) as session:
        job = ScrapeJob(
            status=job_status,
            geo="TR",
            timeframe="today 1-m",
            total_keywords=len(keywords),
            keywords=[
                JobKeyword(keyword=keyword, keyword_normalized=keyword.lower(), position=position)
                for position, keyword in enumerate(keywords)
            ],
        )
        session.add(job)
        session.commit()
        return job.id


def load_job(engine, job_id: int) -> ScrapeJob:
    """Job'ı keyword'leri ve sonuçlarıyla birlikte, oturum kapandıktan sonra da okunabilir halde getirir."""
    with Session(engine) as session:
        job = session.get(ScrapeJob, job_id)
        for keyword in job.keywords:
            keyword.results  # ilişkileri oturum açıkken yükle
        session.expunge_all()
        return job


class RecordingClient:
    """Mock client'ı sarmalar; her fetch sırasında veritabanının o anki halini kaydeder."""

    def __init__(self, settings: Settings, engine):
        self.inner = MockTrendsClient(settings, stable=True)
        self.engine = engine
        self.fetched: list[str] = []
        self.pause_count = 0
        self.snapshots: list[dict] = []

    def fetch_keyword(self, keyword, geo, timeframe):
        # Worker'dan bağımsız bir oturumla, o ana kadar commit edilmiş durumu oku
        with Session(self.engine) as session:
            jobs = session.exec(select(ScrapeJob).order_by(ScrapeJob.id)).all()
            self.snapshots.append(
                {
                    "keyword": keyword,
                    "geo": geo,
                    "timeframe": timeframe,
                    "statuses": [job.status for job in jobs],
                    "processed": [job.processed_keywords for job in jobs],
                }
            )
        self.fetched.append(keyword)
        return self.inner.fetch_keyword(keyword, geo, timeframe)

    def pause_between_keywords(self):
        self.pause_count += 1


# --- Durum kuralları ---

def test_all_keywords_succeed_gives_completed(file_engine, file_settings, trends_client):
    """Tüm keyword'ler başarılıysa job completed olur."""
    job_id = add_job(file_engine, ["Camera Bags", "Backpacks"])

    process_queue(file_engine, trends_client, file_settings)
    job = load_job(file_engine, job_id)

    assert job.status == JobStatus.completed
    assert job.processed_keywords == 2
    assert job.error_message is None
    assert job.started_at is not None
    assert job.finished_at >= job.started_at
    assert [keyword.status for keyword in job.keywords] == [KeywordStatus.success, KeywordStatus.success]
    assert all(keyword.processed_at is not None for keyword in job.keywords)
    assert all(keyword.error_message is None for keyword in job.keywords)


def test_mixed_results_give_partial(file_engine, file_settings, trends_client):
    """En az bir başarılı ve bir başarısız keyword varsa job partial olur; hata nedeni kaydedilir."""
    job_id = add_job(file_engine, ["Camera Bags", "Fail Bags", "Empty Bags"])

    process_queue(file_engine, trends_client, file_settings)
    job = load_job(file_engine, job_id)
    camera, fail, empty = job.keywords

    assert job.status == JobStatus.partial
    assert job.processed_keywords == 3
    assert camera.status == KeywordStatus.success
    assert fail.status == KeywordStatus.failed
    assert fail.error_message == "TrendsError: Mock: bu keyword için tarama hatası simüle edildi"
    assert fail.results == []
    assert empty.status == KeywordStatus.failed
    assert empty.error_message == "EmptyTrendsError: Boş veri döndü (top ve rising her ikisi de boş)"


def test_no_success_gives_failed(file_engine, file_settings, trends_client):
    """Hiçbir keyword başarılı değilse job failed olur ama tüm keyword'ler yine de işlenir."""
    job_id = add_job(file_engine, ["Fail Bags", "Empty Bags"])

    process_queue(file_engine, trends_client, file_settings)
    job = load_job(file_engine, job_id)

    assert job.status == JobStatus.failed
    assert job.processed_keywords == 2
    assert job.finished_at is not None
    # Keyword hataları job seviyesinde hata sayılmaz
    assert job.error_message is None


# --- Sonuçların kaydı ---

def test_results_are_saved_with_rank_and_breakout(file_engine, file_settings):
    """Sonuçlar client'ın verdiği sırayla 1'den başlayan rank ile yazılır; breakout yalnızca rising'de ve eşiğe göre."""

    class FixedClient:
        """Bilinen değerler dönen client."""

        def fetch_keyword(self, keyword, geo, timeframe):
            return KeywordTrends(
                top=[TrendItem("camera bag", 100, "100"), TrendItem("best camera bags", 36, "36")],
                rising=[
                    TrendItem("viral camera bags", 26550, "Breakout"),
                    TrendItem("camera bags 2026", 5000, "Breakout"),
                    TrendItem("cheap camera bags", 4999, "+4,999%"),
                ],
            )

        def pause_between_keywords(self):
            pass

    job_id = add_job(file_engine, ["Camera Bags"])

    process_queue(file_engine, FixedClient(), file_settings)
    results = load_job(file_engine, job_id).keywords[0].results
    saved = sorted(
        (result.type.value, result.rank, result.query, result.value, result.is_breakout) for result in results
    )

    assert saved == [
        ("rising", 1, "viral camera bags", 26550, True),
        ("rising", 2, "camera bags 2026", 5000, True),
        ("rising", 3, "cheap camera bags", 4999, False),
        ("top", 1, "camera bag", 100, False),
        ("top", 2, "best camera bags", 36, False),
    ]


def test_breakout_threshold_comes_from_settings(file_engine, file_settings, trends_client):
    """Eşik yükseltilirse aynı veride breakout sayısı azalır (0 olur)."""
    strict_settings = file_settings.model_copy(update={"BREAKOUT_THRESHOLD": 10_000_000})
    job_id = add_job(file_engine, ["Camera Bags"])

    process_queue(file_engine, trends_client, strict_settings)
    results = load_job(file_engine, job_id).keywords[0].results

    assert any(result.type == QueryType.rising for result in results)
    assert not any(result.is_breakout for result in results)


def test_job_parameters_are_passed_to_client(file_engine, file_settings):
    """Keyword'ler istekteki sırayla, job'ın geo ve timeframe değerleriyle taranır."""
    client = RecordingClient(file_settings, file_engine)
    add_job(file_engine, ["Tote Bags", "Camera Bags", "Backpacks"])

    process_queue(file_engine, client, file_settings)

    assert client.fetched == ["Tote Bags", "Camera Bags", "Backpacks"]
    assert {(snapshot["geo"], snapshot["timeframe"]) for snapshot in client.snapshots} == {("TR", "today 1-m")}


# --- İlerleme ---

def test_progress_is_committed_after_each_keyword(file_engine, file_settings):
    """Her keyword sonrası ilerleme commit edilir; başka bir bağlantı sayacın arttığını görür."""
    client = RecordingClient(file_settings, file_engine)
    job_id = add_job(file_engine, ["Camera Bags", "Fail Bags", "Backpacks"])

    process_queue(file_engine, client, file_settings)

    # Her fetch anında job running ve sayaç o ana kadar biten keyword sayısı kadardır
    assert [snapshot["statuses"] for snapshot in client.snapshots] == [[JobStatus.running]] * 3
    assert [snapshot["processed"] for snapshot in client.snapshots] == [[0], [1], [2]]
    assert load_job(file_engine, job_id).processed_keywords == 3


def test_pause_is_skipped_after_last_keyword(file_engine, file_settings):
    """Keyword'ler arasında beklenir, son keyword'den sonra beklenmez."""
    client = RecordingClient(file_settings, file_engine)
    add_job(file_engine, ["Camera Bags", "Backpacks", "Tote Bags"])
    add_job(file_engine, ["Aprons"])

    process_queue(file_engine, client, file_settings)

    # İlk job'da 2 ara, tek keyword'lü ikinci job'da 0 ara
    assert client.pause_count == 2


# --- Sıralı işleme ve kilit ---

def test_pending_jobs_are_processed_in_order_one_at_a_time(file_engine, file_settings):
    """İki bekleyen job en eskiden başlayarak sırayla işlenir; biri çalışırken diğeri bekler."""
    client = RecordingClient(file_settings, file_engine)
    first_id = add_job(file_engine, ["Camera Bags", "Backpacks"])
    second_id = add_job(file_engine, ["Tote Bags", "Aprons"])

    process_queue(file_engine, client, file_settings)

    assert client.fetched == ["Camera Bags", "Backpacks", "Tote Bags", "Aprons"]
    # İlk job taranırken ikincisi pending; ikincisi taranırken ilki çoktan completed
    assert [snapshot["statuses"] for snapshot in client.snapshots] == [
        [JobStatus.running, JobStatus.pending],
        [JobStatus.running, JobStatus.pending],
        [JobStatus.completed, JobStatus.running],
        [JobStatus.completed, JobStatus.running],
    ]
    first, second = load_job(file_engine, first_id), load_job(file_engine, second_id)
    assert first.finished_at <= second.started_at


def test_second_call_returns_immediately_while_worker_holds_lock(file_engine, file_settings, trends_client):
    """Kilit tutuluyken yapılan çağrı hiçbir job'a dokunmadan döner; kilit bırakılınca job işlenir."""
    job_id = add_job(file_engine, ["Camera Bags"])

    assert job_runner._worker_lock.acquire(blocking=False)
    try:
        process_queue(file_engine, trends_client, file_settings)
        status_while_locked = load_job(file_engine, job_id).status
    finally:
        job_runner._worker_lock.release()
    process_queue(file_engine, trends_client, file_settings)

    assert status_while_locked == JobStatus.pending
    assert load_job(file_engine, job_id).status == JobStatus.completed


def test_concurrent_workers_never_scrape_in_parallel(file_engine, file_settings):
    """Aynı anda başlatılan birden çok worker'da bile taramalar asla üst üste binmez."""

    class OverlapDetectingClient:
        """Aynı anda kaç fetch çalıştığını ölçer."""

        def __init__(self):
            self.inner = MockTrendsClient(file_settings, stable=True)
            self.guard = threading.Lock()
            self.active = 0
            self.max_active = 0
            self.total = 0

        def fetch_keyword(self, keyword, geo, timeframe):
            with self.guard:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                self.total += 1
            time.sleep(0.01)  # üst üste binme olasılığını artır
            with self.guard:
                self.active -= 1
            return self.inner.fetch_keyword(keyword, geo, timeframe)

        def pause_between_keywords(self):
            pass

    client = OverlapDetectingClient()
    job_ids = [add_job(file_engine, [f"Product {number}", f"Item {number}"]) for number in range(4)]

    workers = [
        threading.Thread(target=process_queue, args=(file_engine, client, file_settings)) for _ in range(4)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=THREAD_TIMEOUT)

    assert not any(worker.is_alive() for worker in workers)
    assert client.max_active == 1
    assert client.total == 8
    assert {load_job(file_engine, job_id).status for job_id in job_ids} == {JobStatus.completed}


def test_job_added_during_processing_is_picked_up_by_running_worker(file_engine, file_settings):
    """Worker çalışırken eklenen job, aynı worker tarafından sırası gelince işlenir."""

    class EnqueueingClient:
        """İlk fetch sırasında kuyruğa yeni bir job ekler (kullanıcının tarama sürerken POST atması gibi)."""

        def __init__(self):
            self.inner = MockTrendsClient(file_settings, stable=True)
            self.added_job_id = None

        def fetch_keyword(self, keyword, geo, timeframe):
            if self.added_job_id is None:
                self.added_job_id = add_job(file_engine, ["Late Arrival"])
                # Yeni job'ın tetiklediği çağrı kilidi alamaz ve hemen döner
                process_queue(file_engine, self, file_settings)
            return self.inner.fetch_keyword(keyword, geo, timeframe)

        def pause_between_keywords(self):
            pass

    client = EnqueueingClient()
    first_id = add_job(file_engine, ["Camera Bags"])

    process_queue(file_engine, client, file_settings)

    assert load_job(file_engine, first_id).status == JobStatus.completed
    assert load_job(file_engine, client.added_job_id).status == JobStatus.completed


def test_job_arriving_just_before_lock_release_is_not_left_behind(
    file_engine, file_settings, trends_client, monkeypatch
):
    """Yarış durumu: worker kuyruğu boş görüp kilidi bırakmadan hemen önce gelen job yine de işlenir."""
    original_next_pending = job_runner._next_pending_job
    late_job_ids: list[int] = []

    def next_pending_with_late_arrival(session):
        """Worker kuyruğu ilk kez boş gördüğü anda, o fark etmeden yeni bir job ekler."""
        job = original_next_pending(session)
        if job is None and not late_job_ids:
            late_job_ids.append(add_job(file_engine, ["Late Arrival"]))
            # Yeni job'ın kendi çağrısı kilidi alamadığı için hemen döner
            assert job_runner._worker_lock.locked()
        return job

    monkeypatch.setattr(job_runner, "_next_pending_job", next_pending_with_late_arrival)
    first_id = add_job(file_engine, ["Camera Bags"])

    process_queue(file_engine, trends_client, file_settings)

    assert load_job(file_engine, first_id).status == JobStatus.completed
    assert load_job(file_engine, late_job_ids[0]).status == JobStatus.completed
    assert job_runner._worker_lock.locked() is False


# --- Beklenmeyen hatalar ---

def test_unexpected_error_fails_job_and_worker_continues(file_engine, file_settings):
    """TrendsError dışındaki hata job'ı failed yapar, yarım sonuçlar geri alınır, sıradaki job yine işlenir."""

    class CrashingClient:
        """"Crash" içeren keyword'de beklenmeyen bir hata fırlatır."""

        def __init__(self):
            self.inner = MockTrendsClient(file_settings, stable=True)

        def fetch_keyword(self, keyword, geo, timeframe):
            if "Crash" in keyword:
                raise ZeroDivisionError("beklenmeyen hata")
            return self.inner.fetch_keyword(keyword, geo, timeframe)

        def pause_between_keywords(self):
            pass

    crashed_id = add_job(file_engine, ["Camera Bags", "Crash Bags", "Backpacks"])
    next_id = add_job(file_engine, ["Tote Bags"])

    process_queue(file_engine, CrashingClient(), file_settings)
    crashed, following = load_job(file_engine, crashed_id), load_job(file_engine, next_id)

    assert crashed.status == JobStatus.failed
    assert crashed.error_message == "ZeroDivisionError: beklenmeyen hata"
    assert crashed.finished_at is not None
    # Hatadan önce biten keyword'ün ilerlemesi korunur; sonrakiler işlenmemiş kalır
    assert crashed.processed_keywords == 1
    assert [keyword.status for keyword in crashed.keywords] == [
        KeywordStatus.success, KeywordStatus.pending, KeywordStatus.pending,
    ]
    assert following.status == JobStatus.completed


def test_job_deleted_while_running_does_not_break_worker(file_engine, file_settings):
    """Job çalışırken veritabanından silinirse worker çökmeden sıradaki job'a geçer."""

    class DeletingClient:
        """İlk job'ın taraması sırasında o job'ı siler."""

        def __init__(self):
            self.inner = MockTrendsClient(file_settings, stable=True)
            self.deleted = False

        def fetch_keyword(self, keyword, geo, timeframe):
            if not self.deleted:
                self.deleted = True
                with Session(file_engine) as session:
                    session.delete(session.get(ScrapeJob, doomed_id))
                    session.commit()
            return self.inner.fetch_keyword(keyword, geo, timeframe)

        def pause_between_keywords(self):
            pass

    doomed_id = add_job(file_engine, ["Camera Bags"])
    next_id = add_job(file_engine, ["Backpacks"])

    process_queue(file_engine, DeletingClient(), file_settings)

    with Session(file_engine) as session:
        assert session.get(ScrapeJob, doomed_id) is None
    assert load_job(file_engine, next_id).status == JobStatus.completed


def test_empty_queue_is_a_no_op(file_engine, file_settings, trends_client):
    """Bekleyen job yokken çağrı hiçbir şey yapmadan döner ve kilidi bırakır."""
    process_queue(file_engine, trends_client, file_settings)

    assert job_runner._worker_lock.locked() is False


# --- Startup kurtarma ---

def test_recover_interrupted_jobs_marks_running_as_failed(file_engine):
    """Çalışır durumda kalmış job failed olur; diğer durumlara dokunulmaz."""
    running_id = add_job(file_engine, ["Camera Bags"], JobStatus.running)
    pending_id = add_job(file_engine, ["Backpacks"], JobStatus.pending)
    completed_id = add_job(file_engine, ["Tote Bags"], JobStatus.completed)

    recovered_count = recover_interrupted_jobs(file_engine)
    interrupted = load_job(file_engine, running_id)

    assert recovered_count == 1
    assert interrupted.status == JobStatus.failed
    assert interrupted.error_message == INTERRUPTED_MESSAGE
    assert interrupted.finished_at is not None
    assert load_job(file_engine, pending_id).status == JobStatus.pending
    assert load_job(file_engine, completed_id).status == JobStatus.completed
    assert recover_interrupted_jobs(file_engine) == 0


def test_startup_recovers_and_resumes_queue(file_engine, file_settings, trends_client):
    """Uygulama açılırken yarım kalan job failed olur ve bekleyen job'lar kendiliğinden işlenir."""
    running_id = add_job(file_engine, ["Camera Bags"], JobStatus.running)
    pending_ids = [add_job(file_engine, ["Backpacks", "Tote Bags"]), add_job(file_engine, ["Aprons"])]

    with TestClient(create_app(file_settings, trends_client=trends_client)) as client:
        # Worker ayrı thread'de çalışır; kuyruk boşalana kadar kısa aralıklarla bak
        deadline = time.monotonic() + THREAD_TIMEOUT
        while time.monotonic() < deadline:
            if client.get("/health").json()["queue"] == {"pending": 0, "running": 0}:
                break
            time.sleep(0.01)

        interrupted = client.get(f"/jobs/{running_id}").json()
        resumed = [client.get(f"/jobs/{job_id}").json() for job_id in pending_ids]

    assert interrupted["status"] == "failed"
    assert interrupted["error_message"] == INTERRUPTED_MESSAGE
    assert [job["status"] for job in resumed] == ["completed", "completed"]
    assert [job["progress"] for job in resumed] == ["2/2", "1/1"]


def test_startup_without_pending_jobs_starts_no_worker(file_engine, file_settings, trends_client):
    """Bekleyen job yoksa açılışta worker thread'i başlatılmaz."""
    assert job_runner.resume_pending_jobs(file_engine, trends_client, file_settings) is None
