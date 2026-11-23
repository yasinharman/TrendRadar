# Hata kodu denetimi: her endpoint'in 404 / 409 / 422 davranışı tek tabloda
# Ayrıntılı senaryolar ilgili test dosyalarındadır; bu dosya "her endpoint için en az bir test" güvencesidir

import pytest
from fastapi import status

from app.db.models import JobKeyword, JobStatus, ScrapeJob

NOT_FOUND = status.HTTP_404_NOT_FOUND
CONFLICT = status.HTTP_409_CONFLICT
UNPROCESSABLE = status.HTTP_422_UNPROCESSABLE_CONTENT

# Var olmayan bir job id'si
MISSING = 999999

# (metot, yol, query parametreleri, gövde, beklenen kod)
# Yoldaki {running} ve {completed} yer tutucuları fixture'daki job id'leriyle değiştirilir
ERROR_CASES = [
    # POST /jobs
    ("post", "/jobs", None, {"keywords": []}, UNPROCESSABLE),
    ("post", "/jobs", None, {"keywords": ["Camera Bags"], "geo": "TUR"}, UNPROCESSABLE),
    # GET /jobs
    ("get", "/jobs", {"status": "done"}, None, UNPROCESSABLE),
    ("get", "/jobs", {"limit": 0}, None, UNPROCESSABLE),
    # GET /jobs/{id}
    ("get", f"/jobs/{MISSING}", None, None, NOT_FOUND),
    ("get", "/jobs/abc", None, None, UNPROCESSABLE),
    # DELETE /jobs/{id}
    ("delete", f"/jobs/{MISSING}", None, None, NOT_FOUND),
    ("delete", "/jobs/{running}", None, None, CONFLICT),
    ("delete", "/jobs/abc", None, None, UNPROCESSABLE),
    # GET /jobs/{id}/results
    ("get", f"/jobs/{MISSING}/results", None, None, NOT_FOUND),
    ("get", "/jobs/{completed}/results", {"min_value": -1}, None, UNPROCESSABLE),
    # GET /jobs/{id}/export.csv
    ("get", f"/jobs/{MISSING}/export.csv", None, None, NOT_FOUND),
    ("get", "/jobs/{running}/export.csv", None, None, CONFLICT),
    ("get", "/jobs/{completed}/export.csv", {"type": "trending"}, None, UNPROCESSABLE),
    # GET /keywords/{keyword}/history
    ("get", "/keywords/Hic Taranmadi/history", None, None, NOT_FOUND),
    ("get", "/keywords/Camera Bags/history", {"limit": 0}, None, UNPROCESSABLE),
    # GET /insights/breakouts
    ("get", "/insights/breakouts", {"since": "dün"}, None, UNPROCESSABLE),
    # GET /insights/diff
    ("get", "/insights/diff", {"from_job": "{completed}", "to_job": MISSING}, None, NOT_FOUND),
    ("get", "/insights/diff", {"from_job": "{completed}", "to_job": "{running}"}, None, CONFLICT),
    ("get", "/insights/diff", {"from_job": "{completed}", "to_job": "{completed}"}, None, UNPROCESSABLE),
]


@pytest.fixture
def job_ids(client, session) -> dict[str, str]:
    """Biri tamamlanmış (API üzerinden), biri çalışır durumda (doğrudan yazılmış) iki job."""
    completed_id = client.post("/jobs", json={"keywords": ["Camera Bags"]}).json()["id"]
    running = ScrapeJob(
        status=JobStatus.running,
        geo="",
        timeframe="today 1-m",
        total_keywords=1,
        keywords=[JobKeyword(keyword="Backpacks", keyword_normalized="backpacks", position=0)],
    )
    session.add(running)
    session.commit()
    return {"completed": str(completed_id), "running": str(running.id)}


def fill(value, job_ids: dict[str, str]):
    """Metindeki {running} / {completed} yer tutucularını gerçek id'lerle değiştirir."""
    return value.format(**job_ids) if isinstance(value, str) else value


@pytest.mark.parametrize("method, path, params, body, expected_status", ERROR_CASES)
def test_error_code(client, job_ids, method, path, params, body, expected_status):
    """Endpoint, hatalı durumda beklenen kodu ve `detail` alanı olan bir gövde döner."""
    filled_params = {name: fill(value, job_ids) for name, value in (params or {}).items()}

    response = client.request(method, fill(path, job_ids), params=filled_params, json=body)

    assert response.status_code == expected_status, response.text
    assert response.json()["detail"]


def test_every_endpoint_with_an_error_code_is_covered(client):
    """Tablodaki testler, Swagger'da belgelenen her (endpoint, hata kodu) çiftini kapsar."""
    schema = client.get("/openapi.json").json()
    documented = {
        (method, path, int(code))
        for path, methods in schema["paths"].items()
        for method, operation in methods.items()
        for code in operation["responses"]
        if code in {str(NOT_FOUND), str(CONFLICT), str(UNPROCESSABLE)}
    }

    def template(path: str) -> str:
        """Test yolunu Swagger'daki şablon yola çevirir."""
        parts = path.strip("/").split("/")
        if parts[0] == "jobs" and len(parts) > 1:
            parts[1] = "{job_id}"
        if parts[0] == "keywords":
            parts[1:-1] = ["{keyword}"]
        return "/" + "/".join(parts)

    tested = {(method, template(path), expected_status) for method, path, _, _, expected_status in ERROR_CASES}

    assert documented - tested == set()
