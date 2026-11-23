# /jobs endpoint'lerinin testleri: oluşturma, doğrulama, listeleme, detay ve silme
# TestClient arka plan işlerini yanıttan hemen sonra senkron çalıştırır; bu yüzden POST'tan
# sonraki ilk GET job'ı bitmiş görür

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlmodel import select

from app.core.config import Settings
from app.db.models import JobKeyword, JobStatus, QueryResult, ScrapeJob
from app.main import create_app
from app.services.trends.mock_client import MockTrendsClient


def create_job(client, keywords, **extra) -> dict:
    """Bir tarama başlatır ve POST yanıtının gövdesini döner."""
    response = client.post("/jobs", json={"keywords": keywords, **extra})
    assert response.status_code == status.HTTP_202_ACCEPTED, response.text
    return response.json()


def add_job(session, job_status: JobStatus, keyword: str = "Camera Bags") -> ScrapeJob:
    """Worker'ı tetiklemeden doğrudan veritabanına belirli durumda bir job yazar."""
    job = ScrapeJob(
        status=job_status,
        geo="",
        timeframe="today 1-m",
        total_keywords=1,
        keywords=[JobKeyword(keyword=keyword, keyword_normalized=keyword.lower(), position=0)],
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


# --- POST /jobs: geçerli oluşturma ---

def test_create_job_returns_202_with_location(client):
    """Geçerli istek 202, Location header'ı ve pending durumda job döner."""
    response = client.post(
        "/jobs", json={"keywords": ["Camera Bags", "Backpacks", "Tote Bags"], "geo": "TR", "timeframe": "today 3-m"}
    )
    body = response.json()

    assert response.status_code == status.HTTP_202_ACCEPTED
    assert response.headers["location"] == f"/jobs/{body['id']}"
    assert body["status"] == "pending"
    assert body["geo"] == "TR"
    assert body["timeframe"] == "today 3-m"
    assert body["total_keywords"] == 3
    assert body["processed_keywords"] == 0
    assert body["progress"] == "0/3"
    assert body["queue_position"] == 0
    assert body["error_message"] is None
    assert body["created_at"].endswith("Z")
    assert body["started_at"] is None
    assert body["finished_at"] is None


def test_created_job_is_processed_in_background(client):
    """POST sonrası job arka planda taranır ve completed olur."""
    job = create_job(client, ["Camera Bags", "Backpacks"])

    detail = client.get(f"/jobs/{job['id']}").json()

    assert detail["status"] == "completed"
    assert detail["progress"] == "2/2"
    assert detail["queue_position"] is None
    assert detail["started_at"] is not None
    assert detail["finished_at"] >= detail["started_at"]


def test_defaults_are_applied_when_geo_and_timeframe_are_omitted(client, settings):
    """geo ve timeframe verilmezse ayarlardaki varsayılanlar kullanılır."""
    job = create_job(client, ["Camera Bags"])

    assert job["geo"] == settings.DEFAULT_GEO
    assert job["timeframe"] == settings.DEFAULT_TIMEFRAME


def test_explicit_null_geo_and_timeframe_use_defaults(client, settings):
    """Açıkça null gönderilen alanlar verilmemiş sayılır."""
    job = create_job(client, ["Camera Bags"], geo=None, timeframe=None)

    assert job["geo"] == settings.DEFAULT_GEO
    assert job["timeframe"] == settings.DEFAULT_TIMEFRAME


def test_configured_defaults_are_used(trends_client):
    """Varsayılanlar sabit değil, uygulamaya verilen ayarlardan okunur."""
    custom = Settings(
        _env_file=None, DATABASE_URL="sqlite://", MOCK_DELAY_SECONDS=0, DEFAULT_GEO="DE", DEFAULT_TIMEFRAME="today 12-m"
    )

    with TestClient(create_app(custom, trends_client=trends_client)) as custom_client:
        job = create_job(custom_client, ["Camera Bags"])
        worldwide = create_job(custom_client, ["Camera Bags"], geo="")

    assert job["geo"] == "DE"
    assert job["timeframe"] == "today 12-m"
    # Açıkça boş verilen geo "tüm dünya" demektir; varsayılanla değiştirilmez
    assert worldwide["geo"] == ""


def test_lowercase_geo_is_uppercased(client):
    """Küçük harfli ülke kodu büyük harfe çevrilir."""
    assert create_job(client, ["Camera Bags"], geo="tr")["geo"] == "TR"


# --- POST /jobs: keyword temizleme ---

def test_duplicate_keywords_are_removed(client):
    """Büyük/küçük harf ve boşluk farkı gözetmeden tekrarlar temizlenir; ilk yazılış korunur."""
    job = create_job(client, ["Camera Bags", "camera bags", "  CAMERA   BAGS ", "Backpacks", "backpacks"])

    detail = client.get(f"/jobs/{job['id']}").json()

    assert job["total_keywords"] == 2
    assert [keyword["keyword"] for keyword in detail["keywords"]] == ["Camera Bags", "Backpacks"]


def test_keyword_whitespace_is_normalized(client, session):
    """Keyword'ün kenar boşlukları atılır; normalize hali küçük harfle saklanır."""
    job = create_job(client, ["  Camera    Bags  "])

    stored = session.exec(select(JobKeyword).where(JobKeyword.job_id == job["id"])).one()

    assert stored.keyword == "Camera Bags"
    assert stored.keyword_normalized == "camera bags"
    assert stored.position == 0


# --- POST /jobs: doğrulama hataları ---

@pytest.mark.parametrize(
    "payload",
    [
        {"keywords": []},                                        # boş liste
        {},                                                      # keywords alanı yok
        {"keywords": "Camera Bags"},                             # liste değil
        {"keywords": ["Camera Bags", "   "]},                    # boş keyword
        {"keywords": [""]},                                      # boş keyword
        {"keywords": ["Camera Bags"], "geo": "TUR"},             # 3 harfli ülke kodu
        {"keywords": ["Camera Bags"], "geo": "T1"},              # harf olmayan ülke kodu
        {"keywords": ["Camera Bags"], "timeframe": "today 7-d"},  # Google'ın kabul etmediği aralık
        {"keywords": ["Camera Bags"], "timeframe": "last week"},  # geçersiz aralık
    ],
)
def test_invalid_payload_returns_422(client, session, payload):
    """Geçersiz istek 422 döner ve hiçbir job oluşturulmaz."""
    response = client.post("/jobs", json=payload)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert session.exec(select(ScrapeJob)).all() == []


def test_too_many_keywords_returns_422(client, settings):
    """Keyword sayısı MAX_KEYWORDS_PER_JOB'ı aşarsa 422 döner."""
    keywords = [f"Product {number}" for number in range(settings.MAX_KEYWORDS_PER_JOB + 1)]

    response = client.post("/jobs", json={"keywords": keywords})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json() == {
        "detail": [
            {
                "type": "value_error",
                "loc": ["body", "keywords"],
                "msg": "Bir taramada en fazla 30 keyword gönderilebilir",
            }
        ]
    }


def test_keyword_limit_is_inclusive_and_applies_after_deduplication(client, settings):
    """Tam sınır kadar keyword kabul edilir; tekrarlar sınırdan önce temizlenir."""
    keywords = [f"Product {number}" for number in range(settings.MAX_KEYWORDS_PER_JOB)]

    job = create_job(client, keywords + ["product 0", "PRODUCT 1"])

    assert job["total_keywords"] == settings.MAX_KEYWORDS_PER_JOB


def test_too_long_keyword_returns_422(client, settings):
    """MAX_KEYWORD_LENGTH'i aşan keyword 422 döner; tam sınırdaki kabul edilir."""
    at_limit = "a" * settings.MAX_KEYWORD_LENGTH

    accepted = client.post("/jobs", json={"keywords": [at_limit]})
    rejected = client.post("/jobs", json={"keywords": [at_limit + "a"]})

    assert accepted.status_code == status.HTTP_202_ACCEPTED
    assert rejected.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert rejected.json() == {
        "detail": [
            {"type": "value_error", "loc": ["body", "keywords"], "msg": "Keyword en fazla 100 karakter olabilir"}
        ]
    }


# --- GET /jobs: listeleme ---

def test_list_jobs_newest_first(client):
    """İşler en yeniden eskiye sıralanır ve sayfa zarfıyla döner."""
    first = create_job(client, ["Camera Bags"])
    second = create_job(client, ["Backpacks"])
    third = create_job(client, ["Tote Bags"])

    body = client.get("/jobs").json()

    assert [job["id"] for job in body["items"]] == [third["id"], second["id"], first["id"]]
    assert body["total"] == 3
    assert body["limit"] == 50
    assert body["offset"] == 0


def test_list_jobs_status_filter(client):
    """status filtresi yalnızca o durumdaki işleri döner; total de filtreye uyar."""
    completed = create_job(client, ["Camera Bags"])
    partial = create_job(client, ["Backpacks", "Fail Bags"])
    failed = create_job(client, ["Fail Bags", "Empty Bags"])

    def ids_with(job_status: str) -> list[int]:
        body = client.get("/jobs", params={"status": job_status}).json()
        assert body["total"] == len(body["items"])
        return [job["id"] for job in body["items"]]

    assert ids_with("completed") == [completed["id"]]
    assert ids_with("partial") == [partial["id"]]
    assert ids_with("failed") == [failed["id"]]
    assert ids_with("pending") == []


def test_list_jobs_pagination(client):
    """limit ve offset sayfayı belirler; total tüm kayıt sayısıdır."""
    ids = [create_job(client, [f"Product {number}"])["id"] for number in range(5)]
    newest_first = list(reversed(ids))

    first_page = client.get("/jobs", params={"limit": 2}).json()
    second_page = client.get("/jobs", params={"limit": 2, "offset": 2}).json()
    last_page = client.get("/jobs", params={"limit": 2, "offset": 4}).json()
    beyond = client.get("/jobs", params={"limit": 2, "offset": 10}).json()

    assert [job["id"] for job in first_page["items"]] == newest_first[:2]
    assert [job["id"] for job in second_page["items"]] == newest_first[2:4]
    assert [job["id"] for job in last_page["items"]] == newest_first[4:]
    assert beyond["items"] == []
    assert {page["total"] for page in (first_page, second_page, last_page, beyond)} == {5}
    assert second_page["limit"] == 2
    assert second_page["offset"] == 2


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"offset": -1}, {"status": "done"}])
def test_list_jobs_invalid_parameters_return_422(client, params):
    """Sınır dışı sayfalama değerleri ve geçersiz status 422 döner."""
    assert client.get("/jobs", params=params).status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_list_jobs_accepts_max_page_size(client, settings):
    """limit tam MAX_PAGE_SIZE olabilir."""
    body = client.get("/jobs", params={"limit": settings.MAX_PAGE_SIZE}).json()

    assert body["limit"] == settings.MAX_PAGE_SIZE


def test_queue_position_counts_running_and_earlier_pending_jobs(client, session):
    """Bekleyen işin sırası: kendinden önce bekleyen + çalışan iş sayısı; diğer durumlarda null."""
    running = add_job(session, JobStatus.running)
    first_pending = add_job(session, JobStatus.pending)
    second_pending = add_job(session, JobStatus.pending)
    completed = add_job(session, JobStatus.completed)

    positions = {job["id"]: job["queue_position"] for job in client.get("/jobs").json()["items"]}

    assert positions == {running.id: None, first_pending.id: 1, second_pending.id: 2, completed.id: None}


# --- GET /jobs/{id}: detay ---

def test_job_detail_includes_keyword_summary(client):
    """Detay, her keyword için durum, sonuç sayıları ve hata nedenini içerir."""
    job = create_job(client, ["Camera Bags", "Empty Bags", "Fail Bags"])

    detail = client.get(f"/jobs/{job['id']}").json()
    camera, empty, fail = detail["keywords"]

    assert detail["status"] == "partial"
    assert camera["keyword"] == "Camera Bags"
    assert camera["status"] == "success"
    assert camera["top_count"] >= 5
    assert camera["rising_count"] >= 3
    assert camera["error_message"] is None
    assert empty == {
        "keyword": "Empty Bags",
        "status": "failed",
        "top_count": 0,
        "rising_count": 0,
        "error_message": "EmptyTrendsError: Boş veri döndü (top ve rising her ikisi de boş)",
    }
    assert fail["status"] == "failed"
    assert fail["error_message"].startswith("TrendsError: ")


def test_job_detail_of_pending_job(client, session):
    """Henüz başlamamış işin keyword'leri pending ve sonuçsuz görünür."""
    job = add_job(session, JobStatus.pending)

    detail = client.get(f"/jobs/{job.id}").json()

    assert detail["status"] == "pending"
    assert detail["queue_position"] == 0
    assert detail["keywords"] == [
        {"keyword": "Camera Bags", "status": "pending", "top_count": 0, "rising_count": 0, "error_message": None}
    ]


def test_job_detail_not_found(client):
    """Olmayan iş 404 döner."""
    response = client.get("/jobs/999")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "Job bulunamadı"}


def test_job_id_must_be_integer(client):
    """Sayı olmayan id 422 döner."""
    assert client.get("/jobs/abc").status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


# --- DELETE /jobs/{id} ---

def test_delete_job_removes_keywords_and_results(client, session):
    """Silinen işin keyword'leri ve sonuçları da silinir; diğer işler etkilenmez."""
    job = create_job(client, ["Camera Bags"])
    other = create_job(client, ["Backpacks"])
    other_result_count = len(session.exec(select(QueryResult)).all()) - len(
        session.exec(
            select(QueryResult).join(JobKeyword).where(JobKeyword.job_id == job["id"])
        ).all()
    )

    response = client.delete(f"/jobs/{job['id']}")

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert response.content == b""
    assert client.get(f"/jobs/{job['id']}").status_code == status.HTTP_404_NOT_FOUND
    assert [stored.job_id for stored in session.exec(select(JobKeyword)).all()] == [other["id"]]
    assert len(session.exec(select(QueryResult)).all()) == other_result_count
    assert client.get(f"/jobs/{other['id']}").json()["status"] == "completed"


def test_delete_pending_job_removes_it_from_queue(client, session):
    """Bekleyen iş silinebilir ve kuyruktan çıkar."""
    job = add_job(session, JobStatus.pending)
    assert client.get("/health").json()["queue"]["pending"] == 1

    response = client.delete(f"/jobs/{job.id}")

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert client.get("/health").json()["queue"]["pending"] == 0


def test_delete_running_job_returns_409(client, session):
    """Çalışan iş silinemez."""
    job = add_job(session, JobStatus.running)

    response = client.delete(f"/jobs/{job.id}")

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json() == {"detail": "Çalışan job silinemez"}
    assert client.get(f"/jobs/{job.id}").status_code == status.HTTP_200_OK


def test_delete_job_not_found(client):
    """Olmayan işi silmek 404 döner."""
    assert client.delete("/jobs/999").status_code == status.HTTP_404_NOT_FOUND


# --- /health kuyruk bilgisi ---

def test_health_reports_queue_counts(client, session):
    """/health bekleyen ve çalışan iş sayılarını gösterir."""
    add_job(session, JobStatus.pending)
    add_job(session, JobStatus.pending)
    add_job(session, JobStatus.running)
    add_job(session, JobStatus.completed)

    assert client.get("/health").json()["queue"] == {"pending": 2, "running": 1}


# --- Demo modu ---

def test_default_mock_client_varies_between_scans():
    """Varsayılan (stable olmayan) mock client ile aynı ürünün ikinci taraması farklı sonuç sayıları verebilir
    ama her iki tarama da başarıyla tamamlanır."""
    demo_settings = Settings(_env_file=None, DATABASE_URL="sqlite://", MOCK_DELAY_SECONDS=0)

    with TestClient(create_app(demo_settings, trends_client=MockTrendsClient(demo_settings))) as demo_client:
        first = create_job(demo_client, ["Camera Bags"])
        second = create_job(demo_client, ["Camera Bags"])
        statuses = [demo_client.get(f"/jobs/{job['id']}").json()["status"] for job in (first, second)]

    assert statuses == ["completed", "completed"]
