# History, breakout ve diff testleri
# Karşılaştırılan job'lar bilinen veriyle doğrudan veritabanına yazılır

from datetime import datetime, timezone

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.models import JobKeyword, JobStatus, KeywordStatus, QueryResult, QueryType, ScrapeJob
from app.main import create_app
from app.services.insights import diff_results
from app.services.trends.mock_client import MockTrendsClient

# Tarama zamanları: job id sırası ile zaman sırası bilinçli olarak farklıdır (C en son eklenir ama ortadadır)
SCAN_A = datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)
SCAN_C = datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)
SCAN_B = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)

BREAKOUT_THRESHOLD = 5000


# --- Veri kurulumu ---

def make_keyword(
    keyword: str,
    position: int,
    scanned_at: datetime,
    top: list[tuple[str, int]] = (),
    rising: list[tuple[str, int]] = (),
    keyword_status: KeywordStatus = KeywordStatus.success,
) -> JobKeyword:
    """Verilen top ve rising listeleriyle bir keyword kurar; rank listedeki sıradır."""
    results = [
        QueryResult(type=QueryType.top, rank=rank, query=query, value=value)
        for rank, (query, value) in enumerate(top, start=1)
    ] + [
        QueryResult(
            type=QueryType.rising, rank=rank, query=query, value=value, is_breakout=value >= BREAKOUT_THRESHOLD
        )
        for rank, (query, value) in enumerate(rising, start=1)
    ]
    return JobKeyword(
        keyword=keyword,
        keyword_normalized=" ".join(keyword.split()).lower(),
        position=position,
        status=keyword_status,
        processed_at=scanned_at,
        results=results,
    )


def add_job(session, job_status: JobStatus, geo: str, timeframe: str, keywords: list[JobKeyword]) -> int:
    """Job'ı yazar ve id'sini döner."""
    job = ScrapeJob(status=job_status, geo=geo, timeframe=timeframe, total_keywords=len(keywords), keywords=keywords)
    session.add(job)
    session.commit()
    return job.id


@pytest.fixture
def jobs(session) -> dict[str, int]:
    """Üç tarama: A (eski), B (yeni, farklı geo/timeframe) ve C (arada, yalnızca Camera Bags)."""
    job_a = add_job(
        session, JobStatus.partial, "", "today 1-m",
        [
            make_keyword(
                "Camera Bags", 0, SCAN_A,
                top=[
                    ("camera bag", 100), ("best camera bags", 36), ("Small Camera Bags", 27), ("coach bags", 22),
                ],
                rising=[("mindshift camera bags", 800), ("vanguard camera bags", 80)],
            ),
            make_keyword("Coasters", 1, SCAN_A, top=[("coasters", 100)], rising=[("coasters diy", 180000)]),
            make_keyword("Tote Bags", 2, SCAN_A, keyword_status=KeywordStatus.failed),
        ],
    )
    job_b = add_job(
        session, JobStatus.partial, "TR", "today 3-m",
        [
            make_keyword("Aprons", 0, SCAN_B, top=[("aprons", 100)], rising=[("aprons for women", 6150)]),
            # Aynı ürün farklı yazılışla; sorgulardan biri de farklı harf büyüklüğüyle
            make_keyword(
                "camera bags", 1, SCAN_B,
                top=[
                    ("camera bag", 100), ("best camera bags", 45),
                    ("small camera bags", 27), ("leather camera bags", 12),
                ],
                rising=[("mindshift camera bags", 26550), ("camera bags 2026", 5000)],
            ),
            make_keyword("Tote Bags", 2, SCAN_B, top=[("tote bag", 100)]),
            make_keyword("Coasters", 3, SCAN_B, keyword_status=KeywordStatus.failed),
        ],
    )
    job_c = add_job(
        session, JobStatus.completed, "", "today 1-m",
        [
            make_keyword(
                "Camera Bags", 0, SCAN_C,
                top=[("camera bag", 100)],
                rising=[("viral camera bags", 9000), ("camera bags 2026", 5000)],
            )
        ],
    )
    return {"a": job_a, "b": job_b, "c": job_c}


def get_diff(client, from_job: int, to_job: int, **params) -> dict:
    """Diff endpoint'ini çağırır ve gövdeyi döner."""
    response = client.get("/insights/diff", params={"from_job": from_job, "to_job": to_job, **params})
    assert response.status_code == status.HTTP_200_OK, response.text
    return response.json()


# --- diff_results: saf karşılaştırma fonksiyonu ---

def make_results(items: list[tuple[str, int]]) -> list[QueryResult]:
    """(query, value) listesinden sıralı QueryResult'lar üretir."""
    return [
        QueryResult(type=QueryType.top, rank=rank, query=query, value=value)
        for rank, (query, value) in enumerate(items, start=1)
    ]


def test_diff_results_identical_lists_have_no_changes():
    """Aynı listeler arasında fark yoktur."""
    items = [("camera bag", 100), ("best camera bags", 36)]

    diff = diff_results(make_results(items), make_results(items))

    assert diff.new == [] and diff.dropped == [] and diff.changed == []


def test_diff_results_empty_lists():
    """Boş listeler ve tek taraflı boşluk doğru sınıflanır."""
    items = make_results([("camera bag", 100)])

    assert diff_results([], []).model_dump() == {"new": [], "dropped": [], "changed": []}
    assert [item.query for item in diff_results([], items).new] == ["camera bag"]
    assert [item.query for item in diff_results(items, []).dropped] == ["camera bag"]


def test_diff_results_classifies_new_dropped_and_changed():
    """Yeni giren, çıkan, değeri değişen ve yalnızca sırası değişen aramalar ayrılır."""
    old = make_results([("a", 100), ("b", 60), ("c", 40), ("d", 20)])
    new = make_results([("a", 100), ("c", 40), ("B", 30), ("e", 10)])

    diff = diff_results(old, new).model_dump()

    assert diff["new"] == [{"query": "e", "value": 10}]
    assert diff["dropped"] == [{"query": "d", "value": 20}]
    assert diff["changed"] == [
        # c: değer aynı, sıra 3 → 2
        {"query": "c", "old_value": 40, "new_value": 40, "delta": 0, "old_rank": 3, "new_rank": 2},
        # b: büyük/küçük harf farkına rağmen eşleşir; değer 60 → 30, sıra 2 → 3
        {"query": "B", "old_value": 60, "new_value": 30, "delta": -30, "old_rank": 2, "new_rank": 3},
    ]


# --- GET /insights/diff ---

def test_diff_between_two_scans(client, jobs):
    """Ortak ve her iki taramada başarılı keyword için new/dropped/changed doğru hesaplanır."""
    diff = get_diff(client, jobs["a"], jobs["b"])

    assert diff == {
        "from_job": jobs["a"],
        "to_job": jobs["b"],
        "warnings": ["geo differs: '' → 'TR'", "timeframe differs: 'today 1-m' → 'today 3-m'"],
        "keywords": [
            {
                "keyword": "camera bags",
                "top": {
                    "new": [{"query": "leather camera bags", "value": 12}],
                    "dropped": [{"query": "coach bags", "value": 22}],
                    # "camera bag" hiç değişmediği için listede yok; "small camera bags" aynı değer ve sırada
                    "changed": [
                        {
                            "query": "best camera bags",
                            "old_value": 36,
                            "new_value": 45,
                            "delta": 9,
                            "old_rank": 2,
                            "new_rank": 2,
                        }
                    ],
                },
                "rising": {
                    "new": [{"query": "camera bags 2026", "value": 5000}],
                    "dropped": [{"query": "vanguard camera bags", "value": 80}],
                    "changed": [
                        {
                            "query": "mindshift camera bags",
                            "old_value": 800,
                            "new_value": 26550,
                            "delta": 25750,
                            "old_rank": 1,
                            "new_rank": 1,
                        }
                    ],
                },
            }
        ],
        # Coasters B'de başarısız, Tote Bags A'da başarısız: yalnızca başarılı oldukları tarafta sayılırlar
        "only_in_from": ["Coasters"],
        "only_in_to": ["Aprons", "Tote Bags"],
    }


def test_diff_is_reversible(client, jobs):
    """Yön değişince new ile dropped yer değiştirir ve delta işaret değiştirir."""
    forward = get_diff(client, jobs["a"], jobs["b"])["keywords"][0]
    backward = get_diff(client, jobs["b"], jobs["a"])

    reversed_keyword = backward["keywords"][0]
    assert reversed_keyword["keyword"] == "Camera Bags"
    assert [item["query"] for item in reversed_keyword["top"]["new"]] == ["coach bags"]
    assert [item["query"] for item in reversed_keyword["top"]["dropped"]] == ["leather camera bags"]
    assert reversed_keyword["rising"]["changed"][0]["delta"] == -forward["rising"]["changed"][0]["delta"]
    assert backward["warnings"] == ["geo differs: 'TR' → ''", "timeframe differs: 'today 3-m' → 'today 1-m'"]
    assert backward["only_in_from"] == ["Aprons", "Tote Bags"]
    assert backward["only_in_to"] == ["Coasters"]


def test_diff_without_warnings_when_parameters_match(client, jobs):
    """geo ve timeframe aynıysa uyarı yoktur."""
    diff = get_diff(client, jobs["a"], jobs["c"])

    assert diff["warnings"] == []
    assert [keyword["keyword"] for keyword in diff["keywords"]] == ["Camera Bags"]
    assert diff["only_in_from"] == ["Coasters"]
    assert diff["only_in_to"] == []


@pytest.mark.parametrize("keyword", ["Camera Bags", "  CAMERA   bags "])
def test_diff_keyword_filter(client, jobs, keyword):
    """keyword filtresi karşılaştırmayı tek ürüne indirir; diğer ürünler only_in listelerinde de görünmez."""
    diff = get_diff(client, jobs["a"], jobs["b"], keyword=keyword)

    assert [item["keyword"] for item in diff["keywords"]] == ["camera bags"]
    assert diff["only_in_from"] == []
    assert diff["only_in_to"] == []


def test_diff_keyword_filter_for_keyword_in_one_scan_only(client, jobs):
    """Yalnızca bir taramada olan ürün için karşılaştırma boş, only_in listesi doludur."""
    diff = get_diff(client, jobs["a"], jobs["b"], keyword="aprons")

    assert diff["keywords"] == []
    assert diff["only_in_from"] == []
    assert diff["only_in_to"] == ["Aprons"]


@pytest.mark.parametrize("query_type, empty_type", [("top", "rising"), ("rising", "top")])
def test_diff_type_filter(client, jobs, query_type, empty_type):
    """type filtresi yalnızca o türü karşılaştırır; diğer tür boş döner."""
    full = get_diff(client, jobs["a"], jobs["b"])["keywords"][0]
    filtered = get_diff(client, jobs["a"], jobs["b"], type=query_type)["keywords"][0]

    assert filtered[query_type] == full[query_type]
    assert filtered[empty_type] == {"new": [], "dropped": [], "changed": []}


@pytest.mark.parametrize("missing", ["from_job", "to_job"])
def test_diff_unknown_job_returns_404(client, jobs, missing):
    """Job'lardan biri yoksa 404 döner."""
    params = {"from_job": jobs["a"], "to_job": jobs["b"], missing: 999}

    response = client.get("/insights/diff", params=params)

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "Job not found"}


@pytest.mark.parametrize("job_status", [JobStatus.pending, JobStatus.running, JobStatus.failed])
@pytest.mark.parametrize("side", ["from_job", "to_job"])
def test_diff_with_unfinished_or_failed_job_returns_409(client, session, jobs, job_status, side):
    """Job'lardan biri completed/partial değilse 409 döner."""
    other_id = add_job(session, job_status, "", "today 1-m", [make_keyword("Aprons", 0, SCAN_B)])
    params = {"from_job": jobs["a"], "to_job": jobs["b"], side: other_id}

    response = client.get("/insights/diff", params=params)

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json() == {
        "detail": f"Job {other_id} is '{job_status.value}'; only completed or partial jobs can be compared"
    }


def test_diff_same_job_returns_422(client, jobs):
    """Bir tarama kendisiyle karşılaştırılamaz."""
    response = client.get("/insights/diff", params={"from_job": jobs["a"], "to_job": jobs["a"]})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json() == {
        "detail": [{"type": "value_error", "loc": ["query", "to_job"], "msg": "from_job and to_job must be different"}]
    }


@pytest.mark.parametrize(
    "params",
    [{}, {"from_job": 1}, {"to_job": 2}, {"from_job": "a", "to_job": 2}, {"from_job": 1, "to_job": 2, "type": "x"}],
)
def test_diff_invalid_parameters_return_422(client, params):
    """Eksik veya geçersiz parametreler 422 döner."""
    assert client.get("/insights/diff", params=params).status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_diff_of_repeated_demo_scans_shows_movement():
    """Demo modunda aynı ürünün iki taraması arasında diff anlamlı sonuç verir."""
    demo_settings = Settings(_env_file=None, DATABASE_URL="sqlite://", MOCK_DELAY_SECONDS=0)

    with TestClient(create_app(demo_settings, trends_client=MockTrendsClient(demo_settings))) as demo_client:
        first = demo_client.post("/jobs", json={"keywords": ["Camera Bags", "Backpacks"]}).json()["id"]
        second = demo_client.post("/jobs", json={"keywords": ["Backpacks", "Camera Bags"]}).json()["id"]
        diff = get_diff(demo_client, first, second)

    assert diff["warnings"] == []
    assert [keyword["keyword"] for keyword in diff["keywords"]] == ["Backpacks", "Camera Bags"]
    for keyword in diff["keywords"]:
        for query_type in ("top", "rising"):
            assert keyword[query_type]["new"] or keyword[query_type]["dropped"]
            assert keyword[query_type]["changed"]


# --- GET /insights/breakouts ---

def get_breakouts(client, **params) -> dict:
    """Breakout endpoint'ini çağırır ve gövdeyi döner."""
    response = client.get("/insights/breakouts", params=params)
    assert response.status_code == status.HTTP_200_OK, response.text
    return response.json()


def test_breakouts_are_sorted_by_value_descending(client, jobs):
    """Tüm taramalardaki breakout'lar en yüksek değer önce gelir; eşitlikte yeni tarama öndedir."""
    body = get_breakouts(client)

    assert [(item["seed_keyword"], item["query"], item["value"], item["job_id"]) for item in body["items"]] == [
        ("Coasters", "coasters diy", 180000, jobs["a"]),
        ("camera bags", "mindshift camera bags", 26550, jobs["b"]),
        ("Camera Bags", "viral camera bags", 9000, jobs["c"]),
        ("Aprons", "aprons for women", 6150, jobs["b"]),
        ("camera bags", "camera bags 2026", 5000, jobs["b"]),   # 20 Kasım
        ("Camera Bags", "camera bags 2026", 5000, jobs["c"]),   # 10 Kasım
    ]
    assert body["total"] == 6
    assert body["items"][0]["scanned_at"] == "2026-11-01T10:00:00Z"


def test_breakouts_exclude_non_breakout_results(client, jobs):
    """Eşiğin altındaki rising ve tüm top sonuçlar listede yer almaz."""
    queries = {item["query"] for item in get_breakouts(client)["items"]}

    assert "vanguard camera bags" not in queries
    assert "camera bag" not in queries


@pytest.mark.parametrize("keyword", ["Camera Bags", "camera  BAGS"])
def test_breakouts_keyword_filter(client, jobs, keyword):
    """keyword filtresi ürünü yazılış farkı gözetmeden eşler."""
    body = get_breakouts(client, keyword=keyword)

    assert [item["value"] for item in body["items"]] == [26550, 9000, 5000, 5000]
    assert body["total"] == 4


def test_breakouts_job_filter(client, jobs):
    """job_id filtresi yalnızca o taramanın breakout'larını döner; olmayan job boş liste verir."""
    body = get_breakouts(client, job_id=jobs["b"])

    assert [item["query"] for item in body["items"]] == [
        "mindshift camera bags", "aprons for women", "camera bags 2026",
    ]
    assert {item["job_id"] for item in body["items"]} == {jobs["b"]}
    assert get_breakouts(client, job_id=999) == {"items": [], "total": 0, "limit": 50, "offset": 0}


@pytest.mark.parametrize(
    "since, expected_values",
    [
        ("2026-11-10T10:00:00Z", [26550, 9000, 6150, 5000, 5000]),     # sınır dahil
        ("2026-11-10T10:00:01Z", [26550, 6150, 5000]),
        ("2026-11-15T00:00:00", [26550, 6150, 5000]),                  # saat dilimi yoksa UTC
        ("2026-11-10T13:00:00+03:00", [26550, 9000, 6150, 5000, 5000]),  # 13:00 UTC+3 == 10:00 UTC
        ("2026-12-01T00:00:00Z", []),
    ],
)
def test_breakouts_since_filter(client, jobs, since, expected_values):
    """since, tarama zamanına göre filtreler ve saat dilimini doğru yorumlar."""
    body = get_breakouts(client, since=since)

    assert [item["value"] for item in body["items"]] == expected_values


def test_breakouts_filters_combine_and_paginate(client, jobs):
    """Filtreler birlikte uygulanır; sayfalama sıralamayı korur."""
    combined = get_breakouts(client, keyword="camera bags", since="2026-11-15T00:00:00Z")
    first_page = get_breakouts(client, limit=2)
    second_page = get_breakouts(client, limit=2, offset=2)

    assert [item["value"] for item in combined["items"]] == [26550, 5000]
    assert [item["value"] for item in first_page["items"]] == [180000, 26550]
    assert [item["value"] for item in second_page["items"]] == [9000, 6150]
    assert first_page["total"] == second_page["total"] == 6


def test_breakouts_empty_database(client):
    """Hiç tarama yokken boş sayfa döner."""
    assert get_breakouts(client) == {"items": [], "total": 0, "limit": 50, "offset": 0}


@pytest.mark.parametrize("params", [{"since": "dün"}, {"job_id": "x"}, {"limit": 0}, {"limit": 201}])
def test_breakouts_invalid_parameters_return_422(client, params):
    """Geçersiz tarih, id veya sayfalama değerleri 422 döner."""
    assert client.get("/insights/breakouts", params=params).status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


# --- GET /keywords/{keyword}/history ---

def test_history_lists_scans_newest_first(client, jobs):
    """Geçmiş, tarama zamanına göre (id'ye göre değil) en yeniden eskiye sıralanır."""
    response = client.get("/keywords/Camera Bags/history")
    body = response.json()

    assert response.status_code == status.HTTP_200_OK
    # Yanıttaki yazılış en son taramadaki yazılıştır
    assert body["keyword"] == "camera bags"
    assert [scan["job_id"] for scan in body["scans"]] == [jobs["b"], jobs["c"], jobs["a"]]
    assert [scan["scanned_at"] for scan in body["scans"]] == [
        "2026-11-20T10:00:00Z", "2026-11-10T10:00:00Z", "2026-11-01T10:00:00Z",
    ]


def test_history_scan_content(client, jobs):
    """Her tarama geo, timeframe ve rank sırasına göre top/rising listelerini içerir."""
    newest = client.get("/keywords/camera bags/history").json()["scans"][0]

    assert newest == {
        "job_id": jobs["b"],
        "scanned_at": "2026-11-20T10:00:00Z",
        "geo": "TR",
        "timeframe": "today 3-m",
        "top": [
            {"rank": 1, "query": "camera bag", "value": 100},
            {"rank": 2, "query": "best camera bags", "value": 45},
            {"rank": 3, "query": "small camera bags", "value": 27},
            {"rank": 4, "query": "leather camera bags", "value": 12},
        ],
        "rising": [
            {"rank": 1, "query": "mindshift camera bags", "value": 26550, "is_breakout": True},
            {"rank": 2, "query": "camera bags 2026", "value": 5000, "is_breakout": True},
        ],
    }


@pytest.mark.parametrize("path", ["/keywords/CAMERA%20BAGS/history", "/keywords/%20camera%20%20bags%20/history"])
def test_history_matches_normalized_keyword(client, jobs, path):
    """Ürün adı büyük/küçük harf ve boşluk farkı gözetmeden eşleşir."""
    assert len(client.get(path).json()["scans"]) == 3


def test_history_limit_returns_latest_scans(client, jobs):
    """limit en yeni N taramayı döner."""
    body = client.get("/keywords/Camera Bags/history", params={"limit": 2}).json()

    assert [scan["job_id"] for scan in body["scans"]] == [jobs["b"], jobs["c"]]


@pytest.mark.parametrize("query_type, empty_type", [("top", "rising"), ("rising", "top")])
def test_history_type_filter(client, jobs, query_type, empty_type):
    """type filtresi yalnızca o türü doldurur; diğer liste boş döner."""
    scans = client.get("/keywords/Camera Bags/history", params={"type": query_type}).json()["scans"]

    assert all(scan[query_type] for scan in scans)
    assert all(scan[empty_type] == [] for scan in scans)


def test_history_includes_only_successful_scans(client, jobs):
    """Başarısız taramalar geçmişe girmez."""
    tote_bags = client.get("/keywords/Tote Bags/history").json()
    coasters = client.get("/keywords/Coasters/history").json()

    # Tote Bags A'da, Coasters B'de başarısızdı
    assert [scan["job_id"] for scan in tote_bags["scans"]] == [jobs["b"]]
    assert [scan["job_id"] for scan in coasters["scans"]] == [jobs["a"]]


def test_history_unknown_keyword_returns_404(client, jobs):
    """Hiç taranmamış ürün 404 döner."""
    response = client.get("/keywords/Wine Carriers/history")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "No successful scan found for this keyword"}


def test_history_of_keyword_with_only_failed_scans_returns_404(client, session):
    """Yalnızca başarısız taraması olan ürün de 404 döner."""
    add_job(
        session, JobStatus.failed, "", "today 1-m",
        [make_keyword("Media Organizers", 0, SCAN_A, keyword_status=KeywordStatus.failed)],
    )

    assert client.get("/keywords/Media Organizers/history").status_code == status.HTTP_404_NOT_FOUND


def test_history_accepts_keyword_with_slash(client, session):
    """İçinde "/" geçen ürün adı da sorgulanabilir."""
    add_job(
        session, JobStatus.completed, "", "today 1-m",
        [make_keyword("AC/DC Shirts", 0, SCAN_A, top=[("ac/dc shirt", 100)])],
    )

    body = client.get("/keywords/AC/DC Shirts/history").json()

    assert body["keyword"] == "AC/DC Shirts"
    assert len(body["scans"]) == 1


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"type": "x"}])
def test_history_invalid_parameters_return_422(client, jobs, params):
    """Geçersiz limit veya type 422 döner."""
    response = client.get("/keywords/Camera Bags/history", params=params)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_history_grows_with_each_scan(client):
    """API üzerinden yapılan her yeni tarama geçmişe eklenir."""
    for _ in range(3):
        client.post("/jobs", json={"keywords": ["Camera Bags", "Fail Bags"]})

    history = client.get("/keywords/camera bags/history").json()

    assert history["keyword"] == "Camera Bags"
    assert [scan["job_id"] for scan in history["scans"]] == [3, 2, 1]
    assert client.get("/keywords/fail bags/history").status_code == status.HTTP_404_NOT_FOUND
