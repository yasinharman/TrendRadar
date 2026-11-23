# Sonuç listeleme ve CSV export testleri
# Filtreler bilinen veriyle sınanır: job doğrudan veritabanına yazılır

import csv
import io

import pytest
from fastapi import status

from app.db.models import JobKeyword, JobStatus, KeywordStatus, QueryResult, QueryType, ScrapeJob
from app.routers.jobs import CSV_COLUMNS, csv_safe

# Beklenen sıralama: keyword position → önce top sonra rising → rank
# (seed_keyword, type, rank, query, value, is_breakout)
EXPECTED_ROWS = [
    ("Camera Bags", "top", 1, "camera bag", 100, False),
    ("Camera Bags", "top", 2, "best camera bags", 36, False),
    ("Camera Bags", "top", 3, "small camera bags", 27, False),
    ("Camera Bags", "rising", 1, "mindshift camera bags", 26550, True),
    ("Camera Bags", "rising", 2, "vanguard camera bags", 800, False),
    ("Camera Bags", "rising", 3, "50% off camera_bags", 70, False),
    ("Backpacks", "top", 1, "best backpacks", 100, False),
    ("Backpacks", "top", 2, "school backpacks", 47, False),
    ("Backpacks", "rising", 1, "sanrio backpacks for school", 6150, True),
    ("Backpacks", "rising", 2, "cute backpacks for teens", 400, False),
]


# --- Fixture'lar ve yardımcılar ---

def make_results(seed_keyword: str) -> list[QueryResult]:
    """EXPECTED_ROWS içinden bir seed keyword'ün satırlarını, bilinçli olarak ters sırada üretir."""
    rows = [row for row in EXPECTED_ROWS if row[0] == seed_keyword]
    return [
        QueryResult(type=QueryType(query_type), rank=rank, query=query, value=value, is_breakout=is_breakout)
        for _, query_type, rank, query, value, is_breakout in reversed(rows)
    ]


def add_job(session, job_status: JobStatus, keywords: list[JobKeyword]) -> int:
    """Verilen keyword'lerle bir job yazar ve id'sini döner."""
    job = ScrapeJob(
        status=job_status, geo="TR", timeframe="today 1-m", total_keywords=len(keywords), keywords=keywords
    )
    session.add(job)
    session.commit()
    return job.id


@pytest.fixture
def job_id(session) -> int:
    """İki keyword'lü, sonuçları bilinen tamamlanmış bir job."""
    return add_job(
        session,
        JobStatus.completed,
        [
            # Keyword'ler de ters sırada eklenir; sıralama position'a göre olmalı
            JobKeyword(
                keyword="Backpacks", keyword_normalized="backpacks", position=1,
                status=KeywordStatus.success, results=make_results("Backpacks"),
            ),
            JobKeyword(
                keyword="Camera Bags", keyword_normalized="camera bags", position=0,
                status=KeywordStatus.success, results=make_results("Camera Bags"),
            ),
        ],
    )


def get_results(client, job_id: int, **params) -> dict:
    """Sonuç endpoint'ini çağırır ve gövdeyi döner."""
    response = client.get(f"/jobs/{job_id}/results", params=params)
    assert response.status_code == status.HTTP_200_OK, response.text
    return response.json()


def as_rows(items: list[dict]) -> list[tuple]:
    """Yanıt kayıtlarını EXPECTED_ROWS ile karşılaştırılabilir tuple'lara çevirir."""
    return [
        (item["seed_keyword"], item["type"], item["rank"], item["query"], item["value"], item["is_breakout"])
        for item in items
    ]


def parse_csv(response) -> list[list[str]]:
    """CSV yanıtını (BOM'u atarak) satır listesine çevirir."""
    return list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))


# --- GET /jobs/{id}/results: sıralama ve filtreler ---

def test_results_are_ordered_by_position_type_and_rank(client, job_id):
    """Filtresiz istek tüm sonuçları keyword sırası, top→rising ve rank sırasıyla döner."""
    body = get_results(client, job_id)

    assert as_rows(body["items"]) == EXPECTED_ROWS
    assert body["total"] == len(EXPECTED_ROWS)
    assert body["limit"] == 50
    assert body["offset"] == 0


@pytest.mark.parametrize("query_type", ["top", "rising"])
def test_type_filter(client, job_id, query_type):
    """type filtresi yalnızca o türdeki sonuçları döner."""
    body = get_results(client, job_id, type=query_type)

    assert as_rows(body["items"]) == [row for row in EXPECTED_ROWS if row[1] == query_type]
    assert body["total"] == 5


@pytest.mark.parametrize("keyword", ["Camera Bags", "camera bags", "  CAMERA   bags "])
def test_keyword_filter_is_case_insensitive(client, job_id, keyword):
    """keyword filtresi seed keyword'ü büyük/küçük harf ve boşluk farkı gözetmeden eşler."""
    body = get_results(client, job_id, keyword=keyword)

    assert as_rows(body["items"]) == [row for row in EXPECTED_ROWS if row[0] == "Camera Bags"]


def test_keyword_filter_requires_exact_seed_keyword(client, job_id):
    """keyword filtresi kısmi eşleşme yapmaz; job'da olmayan keyword boş liste döner."""
    assert get_results(client, job_id, keyword="Camera") == {"items": [], "total": 0, "limit": 50, "offset": 0}


@pytest.mark.parametrize("min_value, expected_count", [(0, 10), (100, 6), (101, 4), (6150, 2), (26551, 0)])
def test_min_value_filter(client, job_id, min_value, expected_count):
    """min_value sınırı dahildir."""
    body = get_results(client, job_id, min_value=min_value)

    assert as_rows(body["items"]) == [row for row in EXPECTED_ROWS if row[4] >= min_value]
    assert body["total"] == expected_count


def test_breakout_only_filter(client, job_id):
    """breakout_only yalnızca breakout işaretli sonuçları döner; false verilirse filtre uygulanmaz."""
    breakouts = get_results(client, job_id, breakout_only=True)
    everything = get_results(client, job_id, breakout_only=False)

    assert [item["query"] for item in breakouts["items"]] == ["mindshift camera bags", "sanrio backpacks for school"]
    assert everything["total"] == len(EXPECTED_ROWS)


@pytest.mark.parametrize(
    "search, expected_queries",
    [
        ("school", ["school backpacks", "sanrio backpacks for school"]),
        (
            "BACKPACKS",
            ["best backpacks", "school backpacks", "sanrio backpacks for school", "cute backpacks for teens"],
        ),
        ("best", ["best camera bags", "best backpacks"]),
        ("bulunmayan", []),
        # % ve _ joker değil düz karakter olarak aranır
        ("%", ["50% off camera_bags"]),
        ("_", ["50% off camera_bags"]),
    ],
)
def test_search_filter(client, job_id, search, expected_queries):
    """search, sorgu metninde geçen ifadeyi büyük/küçük harf duyarsız arar."""
    body = get_results(client, job_id, search=search)

    assert [item["query"] for item in body["items"]] == expected_queries
    assert body["total"] == len(expected_queries)


def test_filters_can_be_combined(client, job_id):
    """Birden çok filtre birlikte uygulanır (VE)."""
    body = get_results(client, job_id, type="rising", keyword="backpacks", min_value=500, search="school")

    assert as_rows(body["items"]) == [("Backpacks", "rising", 1, "sanrio backpacks for school", 6150, True)]


# --- GET /jobs/{id}/results: sayfalama ve hatalar ---

def test_results_pagination(client, job_id):
    """Sayfalar sıralamayı korur, çakışmaz ve total filtrelenmiş toplamı gösterir."""
    pages = [get_results(client, job_id, limit=4, offset=offset) for offset in (0, 4, 8, 12)]

    assert [len(page["items"]) for page in pages] == [4, 4, 2, 0]
    assert as_rows([item for page in pages for item in page["items"]]) == EXPECTED_ROWS
    assert {page["total"] for page in pages} == {10}

    filtered = get_results(client, job_id, type="top", limit=2, offset=2)
    assert filtered["total"] == 5
    assert as_rows(filtered["items"]) == [row for row in EXPECTED_ROWS if row[1] == "top"][2:4]


def test_results_of_unknown_job_return_404(client):
    """Olmayan job'ın sonuçları 404 döner."""
    response = client.get("/jobs/999/results")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "Job bulunamadı"}


@pytest.mark.parametrize(
    "params",
    [{"min_value": -1}, {"type": "trending"}, {"breakout_only": "belki"}, {"limit": 0}, {"limit": 201}, {"offset": -1}],
)
def test_invalid_result_filters_return_422(client, job_id, params):
    """Geçersiz filtre ve sayfalama değerleri 422 döner."""
    response = client.get(f"/jobs/{job_id}/results", params=params)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_unfinished_job_returns_results_collected_so_far(client, session):
    """Bekleyen job boş liste, çalışan job o ana kadar biriken sonuçları döner; hata vermez."""
    pending_id = add_job(
        session, JobStatus.pending, [JobKeyword(keyword="Aprons", keyword_normalized="aprons", position=0)]
    )
    running_id = add_job(
        session,
        JobStatus.running,
        [
            JobKeyword(
                keyword="Camera Bags", keyword_normalized="camera bags", position=0,
                status=KeywordStatus.success, results=make_results("Camera Bags"),
            ),
            JobKeyword(keyword="Backpacks", keyword_normalized="backpacks", position=1),
        ],
    )

    assert get_results(client, pending_id)["total"] == 0
    assert as_rows(get_results(client, running_id)["items"]) == [
        row for row in EXPECTED_ROWS if row[0] == "Camera Bags"
    ]


def test_results_of_scanned_job_match_job_detail(client):
    """API üzerinden başlatılan taramanın sonuç sayıları job detayındaki sayılarla tutarlıdır."""
    created = client.post("/jobs", json={"keywords": ["Camera Bags", "Fail Bags", "Backpacks"]}).json()

    detail = client.get(f"/jobs/{created['id']}").json()
    results = get_results(client, created["id"], limit=200)

    assert detail["status"] == "partial"
    assert results["total"] == sum(keyword["top_count"] + keyword["rising_count"] for keyword in detail["keywords"])
    # Başarısız keyword'ün sonucu yoktur; sıra istekteki sırayı izler
    seed_order = list(dict.fromkeys(item["seed_keyword"] for item in results["items"]))
    assert seed_order == ["Camera Bags", "Backpacks"]
    assert all(item["is_breakout"] is False for item in results["items"] if item["type"] == "top")
    assert any(item["is_breakout"] for item in results["items"] if item["type"] == "rising")


# --- GET /jobs/{id}/export.csv ---

def test_csv_headers(client, job_id):
    """CSV dosya olarak, doğru içerik türü ve dosya adıyla sunulur."""
    response = client.get(f"/jobs/{job_id}/export.csv")

    assert response.status_code == status.HTTP_200_OK
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"] == f'attachment; filename="trendradar_job_{job_id}.csv"'


def test_csv_content(client, job_id):
    """CSV, UTF-8 BOM ile başlar; başlık ve satırlar sonuç endpoint'iyle aynı sırada ve içeriktedir."""
    response = client.get(f"/jobs/{job_id}/export.csv")
    rows = parse_csv(response)

    assert response.content.startswith(b"\xef\xbb\xbf")
    assert rows[0] == CSV_COLUMNS == ["seed_keyword", "type", "rank", "query", "value", "is_breakout"]
    assert len(rows) == 1 + len(EXPECTED_ROWS)
    assert rows[1:] == [
        [seed_keyword, query_type, str(rank), query, str(value), str(is_breakout).lower()]
        for seed_keyword, query_type, rank, query, value, is_breakout in EXPECTED_ROWS
    ]


@pytest.mark.parametrize("query_type", ["top", "rising"])
def test_csv_type_filter(client, job_id, query_type):
    """type parametresi eski scraper'ın iki ayrı CSV'sinin karşılığını verir."""
    rows = parse_csv(client.get(f"/jobs/{job_id}/export.csv", params={"type": query_type}))

    assert len(rows) == 1 + 5
    assert {row[1] for row in rows[1:]} == {query_type}


def test_csv_is_not_paginated(client, session):
    """CSV varsayılan sayfa boyutundan bağımsız olarak tüm satırları içerir."""
    row_count = 120
    job_id = add_job(
        session,
        JobStatus.completed,
        [
            JobKeyword(
                keyword="Camera Bags", keyword_normalized="camera bags", position=0, status=KeywordStatus.success,
                results=[
                    QueryResult(type=QueryType.top, rank=rank, query=f"query {rank}", value=1)
                    for rank in range(1, row_count + 1)
                ],
            )
        ],
    )

    rows = parse_csv(client.get(f"/jobs/{job_id}/export.csv"))

    assert len(rows) == 1 + row_count
    assert [row[2] for row in rows[1:]] == [str(rank) for rank in range(1, row_count + 1)]


def test_csv_escapes_special_characters_and_formulas(client, session):
    """Virgül, tırnak ve Türkçe karakterler korunur; formül gibi başlayan hücreler etkisizleştirilir."""
    queries = [
        'çanta, "deri" ışıltılı', "=HYPERLINK(\"http://x\")", "+90 çanta", "-indirim", "@kampanya", "normal-sorgu",
    ]
    job_id = add_job(
        session,
        JobStatus.completed,
        [
            JobKeyword(
                keyword="=Çanta", keyword_normalized="=çanta", position=0, status=KeywordStatus.success,
                results=[
                    QueryResult(type=QueryType.top, rank=rank, query=query, value=10)
                    for rank, query in enumerate(queries, start=1)
                ],
            )
        ],
    )

    rows = parse_csv(client.get(f"/jobs/{job_id}/export.csv"))

    assert {row[0] for row in rows[1:]} == {"'=Çanta"}
    assert [row[3] for row in rows[1:]] == [
        'çanta, "deri" ışıltılı',
        "'=HYPERLINK(\"http://x\")",
        "'+90 çanta",
        "'-indirim",
        "'@kampanya",
        "normal-sorgu",
    ]
    # JSON yanıtı veriyi değiştirmeden döner; kaçış yalnızca CSV'ye özgüdür
    assert get_results(client, job_id)["items"][1]["query"] == "=HYPERLINK(\"http://x\")"


@pytest.mark.parametrize("text, expected", [("camera bag", "camera bag"), ("", ""), ("=1+1", "'=1+1"), ("\tx", "'\tx")])
def test_csv_safe(text, expected):
    """csv_safe yalnızca formül başlatan karakterle başlayan metinlere dokunur."""
    assert csv_safe(text) == expected


def test_csv_of_partial_job_is_available(client):
    """Partial job'ın CSV'si indirilebilir ve yalnızca başarılı keyword'lerin satırlarını içerir."""
    created = client.post("/jobs", json={"keywords": ["Camera Bags", "Fail Bags"]}).json()

    response = client.get(f"/jobs/{created['id']}/export.csv")
    rows = parse_csv(response)

    assert response.status_code == status.HTTP_200_OK
    assert len(rows) - 1 == get_results(client, created["id"])["total"]
    assert {row[0] for row in rows[1:]} == {"Camera Bags"}


@pytest.mark.parametrize("job_status", [JobStatus.pending, JobStatus.running, JobStatus.failed])
def test_csv_of_unfinished_or_failed_job_returns_409(client, session, job_status):
    """Tamamlanmamış veya başarısız job için CSV 409 döner."""
    job_id = add_job(
        session, job_status, [JobKeyword(keyword="Aprons", keyword_normalized="aprons", position=0)]
    )

    response = client.get(f"/jobs/{job_id}/export.csv")

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json() == {
        "detail": f"Job '{job_status.value}' durumunda; CSV yalnızca completed veya partial işler için indirilebilir"
    }


def test_csv_of_unknown_job_returns_404(client):
    """Olmayan job'ın CSV'si 404 döner."""
    assert client.get("/jobs/999/export.csv").status_code == status.HTTP_404_NOT_FOUND


def test_csv_invalid_type_returns_422(client, job_id):
    """Geçersiz type değeri 422 döner."""
    response = client.get(f"/jobs/{job_id}/export.csv", params={"type": "trending"})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
