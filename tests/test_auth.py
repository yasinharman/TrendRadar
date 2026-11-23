# Parola koruması (HTTP Basic Auth) testleri

import base64
from collections.abc import Iterator

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.core.auth import AUTH_CHALLENGE, UNAUTHORIZED_MESSAGE, credentials_match, parse_basic_credentials
from app.core.config import Settings
from app.main import create_app
from app.services.trends.mock_client import MockTrendsClient

# Testlerde kullanılan kimlik bilgileri (parola ":" ve Türkçe karakter içerir)
USERNAME = "admin"
PASSWORD = "çok:gizli parola"


def basic_header(username: str, password: str) -> str:
    """Verilen kimlik bilgileri için Authorization başlığının değerini üretir."""
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


@pytest.fixture
def protected_client() -> Iterator[TestClient]:
    """Parola koruması açık kurulmuş uygulamaya istek atan istemci."""
    settings = Settings(
        _env_file=None,
        TRENDS_MODE="mock",
        DATABASE_URL="sqlite://",
        MOCK_DELAY_SECONDS=0,
        AUTH_USERNAME=USERNAME,
        AUTH_PASSWORD=PASSWORD,
    )
    app = create_app(settings, trends_client=MockTrendsClient(settings, stable=True))
    with TestClient(app) as test_client:
        yield test_client


# --- Başlığın çözülmesi ---

def test_parse_returns_username_and_password():
    """Geçerli başlıktan kullanıcı adı ve parola çıkar; paroladaki ':' korunur."""
    assert parse_basic_credentials(basic_header(USERNAME, PASSWORD)) == (USERNAME, PASSWORD)


def test_parse_accepts_any_scheme_case():
    """Şema adı büyük/küçük harfe duyarsızdır."""
    header = basic_header(USERNAME, PASSWORD).replace("Basic", "bAsIc")

    assert parse_basic_credentials(header) == (USERNAME, PASSWORD)


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "Bearer abc",
        "Basic",
        "Basic bu-base64-değil!",
        "Basic " + base64.b64encode(b"\xff\xfe").decode("ascii"),
        "Basic " + base64.b64encode(b"ayirici-yok").decode("ascii"),
    ],
)
def test_parse_rejects_malformed_headers(header):
    """Eksik, başka şemalı veya çözülemeyen başlık None döner."""
    assert parse_basic_credentials(header) is None


@pytest.mark.parametrize(
    ("username", "password", "expected"),
    [
        (USERNAME, PASSWORD, True),
        (USERNAME, "yanlış", False),
        ("baskasi", PASSWORD, False),
        (USERNAME, "", False),
    ],
)
def test_credentials_match(username, password, expected):
    """Yalnızca kullanıcı adı ve parolanın ikisi de doğruysa eşleşir."""
    assert credentials_match(basic_header(username, password), USERNAME, PASSWORD) is expected


# --- Korunan uygulama ---

@pytest.mark.parametrize("path", ["/jobs", "/docs", "/openapi.json", "/insights/breakouts"])
def test_request_without_credentials_is_rejected(protected_client, path):
    """Kimlik bilgisi olmayan istek 401 alır ve tarayıcıya parola sorması söylenir."""
    response = protected_client.get(path)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json() == {"detail": UNAUTHORIZED_MESSAGE}
    assert response.headers["www-authenticate"] == AUTH_CHALLENGE


@pytest.mark.parametrize(
    "header",
    [basic_header(USERNAME, "yanlış"), basic_header("baskasi", PASSWORD), "Bearer abc", "Basic bozuk!"],
)
def test_wrong_credentials_are_rejected(protected_client, header):
    """Yanlış veya bozuk kimlik bilgisi 401 alır."""
    response = protected_client.get("/jobs", headers={"Authorization": header})

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_write_request_without_credentials_does_not_create_job(protected_client):
    """Kimlik bilgisi olmadan tarama başlatılamaz."""
    response = protected_client.post("/jobs", json={"keywords": ["Camera Bags"]})

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    listing = protected_client.get("/jobs", headers={"Authorization": basic_header(USERNAME, PASSWORD)})
    assert listing.json()["total"] == 0


def test_correct_credentials_are_accepted(protected_client):
    """Doğru kimlik bilgisiyle API normal çalışır."""
    headers = {"Authorization": basic_header(USERNAME, PASSWORD)}

    created = protected_client.post("/jobs", json={"keywords": ["Camera Bags"]}, headers=headers)
    docs = protected_client.get("/docs", headers=headers)

    assert created.status_code == status.HTTP_202_ACCEPTED
    assert docs.status_code == status.HTTP_200_OK


def test_health_is_open_without_credentials(protected_client):
    """Sağlık kontrolü parolasız çalışır (konteyner healthcheck'i için)."""
    response = protected_client.get("/health")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["status"] == "ok"


def test_auth_is_disabled_when_not_configured(client):
    """Kullanıcı adı ve parola ayarlanmadıysa koruma kapalıdır."""
    assert client.get("/jobs").status_code == status.HTTP_200_OK


def test_auth_enabled_property():
    """auth_enabled yalnızca iki ayar da doluyken True döner."""
    assert Settings(_env_file=None).auth_enabled is False
    assert Settings(_env_file=None, AUTH_USERNAME=USERNAME, AUTH_PASSWORD=PASSWORD).auth_enabled is True
