# /health endpoint'i, Swagger ve tablo oluşturma testleri

from fastapi import status
from sqlalchemy import inspect
from sqlalchemy.exc import OperationalError

from app.dependencies import get_session


def test_health_returns_service_info(client):
    """Sağlıklı durumda mod ve veritabanı bilgisi döner."""
    response = client.get("/health")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "status": "ok",
        "app": "TrendRadar API",
        "mode": "mock",
        "database": "sqlite",
        "queue": {"pending": 0, "running": 0},
    }


def test_health_returns_503_when_database_is_down(client):
    """Veritabanı sorgusu başarısız olursa 503 döner."""

    class BrokenSession:
        """Her sorguda bağlantı hatası fırlatan sahte oturum."""

        def exec(self, *args, **kwargs):
            raise OperationalError("SELECT 1", {}, Exception("bağlantı yok"))

    # Gerçek oturumun yerine bozuk oturumu enjekte et
    client.app.dependency_overrides[get_session] = lambda: BrokenSession()

    response = client.get("/health")

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json() == {"detail": "Veritabanına ulaşılamıyor"}


def test_swagger_docs_are_served(client):
    """Swagger arayüzü ve OpenAPI şeması erişilebilir, /health belgelenmiş."""
    docs = client.get("/docs")
    schema = client.get("/openapi.json").json()

    assert docs.status_code == status.HTTP_200_OK
    assert schema["info"]["title"] == "TrendRadar API"
    assert schema["paths"]["/health"]["get"]["summary"] == "Servis durumu"


def test_tables_are_created_on_startup(engine):
    """Uygulama açılırken üç tablo da oluşturulur."""
    tables = set(inspect(engine).get_table_names())

    assert {"scrape_jobs", "job_keywords", "query_results"} <= tables
