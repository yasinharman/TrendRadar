# Web arayüzünün sunulması ve API ile tutarlılığı
# Arayüzün tarayıcıdaki davranışı elle doğrulanır; buradaki testler dosyaların sunulduğunu ve
# arayüzdeki sabit listelerin API'deki tanımlarla aynı kaldığını denetler

import base64
import re
from typing import get_args

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.core.config import GEO_PATTERN, Settings, Timeframe
from app.db.models import JobStatus, KeywordStatus, QueryType
from app.main import create_app
from app.routers.ui import REVALIDATE_ALWAYS, STATIC_DIR, UI_PATH
from app.services.trends.mock_client import MockTrendsClient

# HTML olarak yorumlanan yazma yöntemleri: API'den gelen metinler bunlarla yazılırsa XSS oluşur
UNSAFE_DOM_WRITES = [".innerHTML", ".outerHTML", "insertAdjacentHTML", "document.write"]

# ISO 3166-1 alpha-2 standardındaki ülke/bölge kodu sayısı
ISO_COUNTRY_COUNT = 249


def read_static(name: str) -> str:
    """Arayüz dosyasının içeriğini okur."""
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def label_map(script: str, name: str) -> set[str]:
    """app.js içindeki `const NAME = { ... }` nesnesinin anahtarlarını döner."""
    block = re.search(rf"const {name} = \{{(.*?)\}};", script, re.DOTALL).group(1)
    return {key.strip('"') for key in re.findall(r'^\s*("[^"]+"|\w+):', block, re.MULTILINE)}


# --- Sunum ---

def test_root_redirects_to_ui(client):
    """Kök adres web arayüzüne yönlendirir."""
    response = client.get("/", follow_redirects=False)

    assert response.status_code == status.HTTP_307_TEMPORARY_REDIRECT
    assert response.headers["location"] == f"{UI_PATH}/"


def test_ui_index_is_served(client):
    """Arayüzün ana sayfası HTML olarak sunulur."""
    response = client.get(f"{UI_PATH}/")

    assert response.status_code == status.HTTP_200_OK
    assert response.headers["content-type"].startswith("text/html")
    assert "<title>TrendRadar</title>" in response.text


@pytest.mark.parametrize(
    ("name", "content_type"),
    [("app.js", "javascript"), ("style.css", "text/css")],
)
def test_ui_assets_are_served(client, name, content_type):
    """Ana sayfanın yüklediği betik ve stil dosyası sunulur."""
    response = client.get(f"{UI_PATH}/{name}")

    assert response.status_code == status.HTTP_200_OK
    assert content_type in response.headers["content-type"]
    assert f'"{name}"' in read_static("index.html")


@pytest.mark.parametrize("name", ["", "app.js", "style.css"])
def test_ui_files_are_always_revalidated(client, name):
    """Tarayıcı arayüz dosyalarını sormadan önbellekten kullanamaz; değişmeyen dosya için 304 alır."""
    first = client.get(f"{UI_PATH}/{name}")

    assert first.headers["cache-control"] == REVALIDATE_ALWAYS

    # Tarayıcının elindeki kopya günceldir: gövde yeniden gönderilmez
    second = client.get(f"{UI_PATH}/{name}", headers={"If-None-Match": first.headers["etag"]})

    assert second.status_code == status.HTTP_304_NOT_MODIFIED
    assert second.headers["cache-control"] == REVALIDATE_ALWAYS
    assert second.content == b""


def test_unknown_ui_file_returns_404(client):
    """Var olmayan dosya 404 döner."""
    assert client.get(f"{UI_PATH}/yok.js").status_code == status.HTTP_404_NOT_FOUND


def test_ui_is_not_part_of_openapi(client):
    """Arayüz yolları API belgesinde görünmez."""
    paths = client.get("/openapi.json").json()["paths"]

    assert "/" not in paths
    assert not any(path.startswith(UI_PATH) for path in paths)


def test_ui_requires_password_when_auth_is_enabled():
    """Parola koruması açıkken arayüz dosyaları da kimlik bilgisi ister."""
    settings = Settings(
        _env_file=None,
        TRENDS_MODE="mock",
        DATABASE_URL="sqlite://",
        MOCK_DELAY_SECONDS=0,
        AUTH_USERNAME="admin",
        AUTH_PASSWORD="gizli",
    )
    token = base64.b64encode(b"admin:gizli").decode("ascii")

    with TestClient(create_app(settings, trends_client=MockTrendsClient(settings, stable=True))) as protected:
        for path in ("/", f"{UI_PATH}/", f"{UI_PATH}/app.js"):
            assert protected.get(path, follow_redirects=False).status_code == status.HTTP_401_UNAUTHORIZED
        allowed = protected.get(f"{UI_PATH}/", headers={"Authorization": f"Basic {token}"})

    assert allowed.status_code == status.HTTP_200_OK


# --- API ile tutarlılık ---

def test_timeframe_options_match_api():
    """Formdaki zaman aralığı seçenekleri API'nin kabul ettikleriyle aynıdır (boş = sunucu varsayılanı)."""
    select = re.search(r'<select id="job-timeframe">(.*?)</select>', read_static("index.html"), re.DOTALL).group(1)
    options = re.findall(r'<option value="([^"]*)">', select)

    assert options == ["", *get_args(Timeframe)]


def test_script_labels_cover_api_values():
    """Arayüz, API'nin döndürebildiği her durum, tür ve zaman aralığı için bir etiket tanımlar."""
    script = read_static("app.js")

    assert label_map(script, "JOB_STATUS_LABELS") == {item.value for item in JobStatus}
    assert label_map(script, "KEYWORD_STATUS_LABELS") == {item.value for item in KeywordStatus}
    assert label_map(script, "TYPE_LABELS") == {item.value for item in QueryType}
    assert label_map(script, "TIMEFRAME_LABELS") == set(get_args(Timeframe))


def test_country_codes_are_valid_and_complete():
    """Ülke listesi ISO 3166-1 kodlarının tamamını içerir; her kod API'nin kabul ettiği biçimdedir."""
    block = re.search(r"const COUNTRY_CODES = \((.*?)\)\.split", read_static("app.js"), re.DOTALL).group(1)
    codes = " ".join(re.findall(r'"([^"]*)"', block)).split()

    assert len(codes) == ISO_COUNTRY_COUNT
    assert len(set(codes)) == len(codes)
    assert codes == sorted(codes)
    assert all(re.fullmatch(GEO_PATTERN, code) and code for code in codes)
    assert {"TR", "US", "DE", "GB", "JP"} <= set(codes)


def test_country_field_is_a_dropdown_defaulting_to_worldwide():
    """Ülke elle yazılmaz; açılır listeden seçilir ve ilk seçenek tüm dünyadır."""
    page = read_static("index.html")

    assert re.search(r'<select id="job-geo">\s*(<!--.*?-->\s*)?<option value="">Worldwide</option>', page)
    assert '<input id="job-geo"' not in page


# --- Güvenlik ---

def test_script_never_writes_html():
    """Betik, API'den gelen metni HTML olarak yorumlayan hiçbir yöntem kullanmaz."""
    script = read_static("app.js")

    for unsafe in UNSAFE_DOM_WRITES:
        assert unsafe not in script, unsafe


def test_page_has_no_inline_script_or_handlers():
    """Sayfa içerik güvenlik politikası tanımlar; satır içi betik ve olay niteliği içermez."""
    page = read_static("index.html")

    assert "Content-Security-Policy" in page
    assert re.findall(r"<script(?![^>]*\bsrc=)", page) == []
    assert re.findall(r"\son\w+\s*=", page) == []
