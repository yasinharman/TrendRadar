# Veritabanı modellerinin varsayılanları, ilişkileri ve zaman damgası davranışı

import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.pool import QueuePool, StaticPool
from sqlmodel import Session, select

from app.db.models import (
    JobKeyword,
    JobStatus,
    KeywordStatus,
    QueryResult,
    QueryType,
    ScrapeJob,
    UTCDateTime,
)
from app.db.session import create_db_engine, init_db


def make_job(**overrides) -> ScrapeJob:
    """Testler için içinde iki keyword ve sonuçları olan bir job kurar."""
    # İkinci keyword bilinçli olarak önce eklenir; sıralama position'a göre olmalı
    backpacks = JobKeyword(keyword="Backpacks", keyword_normalized="backpacks", position=1)
    camera_bags = JobKeyword(
        keyword="Camera Bags",
        keyword_normalized="camera bags",
        position=0,
        status=KeywordStatus.success,
        results=[
            QueryResult(type=QueryType.top, rank=1, query="camera bag", value=100),
            QueryResult(type=QueryType.rising, rank=1, query="mindshift camera bags", value=8000, is_breakout=True),
        ],
    )
    fields = {"geo": "TR", "timeframe": "today 1-m", "total_keywords": 2, "keywords": [backpacks, camera_bags]}
    fields.update(overrides)
    return ScrapeJob(**fields)


def test_job_defaults(session):
    """Yeni job pending başlar, ilerleme sıfırdır, zaman damgaları boş veya şimdidir."""
    job = make_job()
    session.add(job)
    session.commit()
    session.refresh(job)

    assert job.id is not None
    assert job.status == JobStatus.pending
    assert job.processed_keywords == 0
    assert job.error_message is None
    assert job.started_at is None
    assert job.finished_at is None
    assert datetime.now(timezone.utc) - job.created_at < timedelta(minutes=1)


def test_timestamps_are_stored_and_returned_as_utc(session):
    """Farklı saat diliminde verilen zaman UTC'ye çevrilir ve UTC bilgili okunur."""
    # 16:30 UTC+3 == 13:30 UTC
    istanbul = timezone(timedelta(hours=3))
    job = make_job(started_at=datetime(2026, 11, 23, 16, 30, tzinfo=istanbul))
    session.add(job)
    session.commit()
    session.expire_all()

    loaded = session.get(ScrapeJob, job.id)

    assert loaded.started_at == datetime(2026, 11, 23, 13, 30, tzinfo=timezone.utc)
    assert loaded.started_at.utcoffset() == timedelta(0)
    assert loaded.created_at.utcoffset() == timedelta(0)


def test_utc_datetime_normalizes_values_from_any_database():
    """PostgreSQL saat dilimli, SQLite saat dilimsiz değer döndürür; ikisi de UTC'ye çevrilir."""
    column_type = UTCDateTime()
    istanbul = timezone(timedelta(hours=3))
    expected = datetime(2026, 11, 23, 13, 30, tzinfo=timezone.utc)

    from_postgres = column_type.process_result_value(datetime(2026, 11, 23, 16, 30, tzinfo=istanbul), dialect=None)
    from_sqlite = column_type.process_result_value(datetime(2026, 11, 23, 13, 30), dialect=None)

    assert from_postgres == expected and from_postgres.utcoffset() == timedelta(0)
    assert from_sqlite == expected and from_sqlite.utcoffset() == timedelta(0)
    assert column_type.process_result_value(None, dialect=None) is None
    assert column_type.process_bind_param(None, dialect=None) is None


@pytest.mark.parametrize(
    "url, expected_dialect, expected_pool",
    [
        ("sqlite://", "sqlite", StaticPool),
        ("sqlite:///:memory:", "sqlite", StaticPool),
        ("postgresql+psycopg://user:secret@localhost:5432/trendradar", "postgresql", QueuePool),
    ],
)
def test_create_db_engine_picks_settings_for_database(url, expected_dialect, expected_pool):
    """Bellek içi SQLite tek bağlantı paylaşır; PostgreSQL ek ayar almaz. Engine kurulurken bağlanılmaz."""
    engine = create_db_engine(url)

    assert engine.dialect.name == expected_dialect
    assert isinstance(engine.pool, expected_pool)


def test_file_based_sqlite_engine_allows_other_threads(tmp_path):
    """Dosya tabanlı SQLite, worker thread'inin de kullanabilmesi için thread kontrolü kapalı açılır."""
    engine = create_db_engine(f"sqlite:///{tmp_path / 'app.db'}")
    init_db(engine)
    errors: list[Exception] = []

    def query_from_other_thread():
        try:
            with Session(engine) as other_session:
                other_session.exec(select(ScrapeJob)).all()
        except Exception as error:  # thread içindeki hata ana thread'e taşınır
            errors.append(error)

    # Bağlantıyı ana thread'de aç, sonra aynı havuzu başka thread'den kullan
    with Session(engine) as main_session:
        main_session.exec(select(ScrapeJob)).all()
    worker = threading.Thread(target=query_from_other_thread)
    worker.start()
    worker.join()
    engine.dispose()

    assert errors == []


def test_status_is_stored_as_lowercase_value(session):
    """Enum veritabanında API'deki metinle aynı şekilde saklanır."""
    job = make_job(status=JobStatus.partial)
    session.add(job)
    session.commit()

    stored = session.exec(text("SELECT status FROM scrape_jobs")).scalar_one()

    assert stored == "partial"


def test_keywords_are_ordered_by_position(session):
    """Job'ın keyword'leri eklenme sırasına değil position'a göre gelir."""
    job = make_job()
    session.add(job)
    session.commit()
    session.expire_all()

    loaded = session.get(ScrapeJob, job.id)

    assert [keyword.keyword for keyword in loaded.keywords] == ["Camera Bags", "Backpacks"]
    assert loaded.keywords[1].status == KeywordStatus.pending
    assert loaded.keywords[0].job is loaded


def test_deleting_job_cascades_to_keywords_and_results(session):
    """Job silinince keyword'leri ve sonuçları da silinir."""
    job = make_job()
    session.add(job)
    session.commit()
    assert len(session.exec(select(QueryResult)).all()) == 2

    session.delete(job)
    session.commit()

    assert session.exec(select(ScrapeJob)).all() == []
    assert session.exec(select(JobKeyword)).all() == []
    assert session.exec(select(QueryResult)).all() == []
