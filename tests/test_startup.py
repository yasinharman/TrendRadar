# Uygulama açılışında trends client seçimi testleri

import json

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import build_trends_client, create_app
from app.services.trends.live_client import LiveTrendsClient
from app.services.trends.mock_client import MockTrendsClient


def test_mock_mode_builds_mock_client():
    """Varsayılan (mock) modda cookie gerekmeden mock client oluşturulur."""
    settings = Settings(_env_file=None, TRENDS_MODE="mock", DATABASE_URL="sqlite://")

    with TestClient(create_app(settings)) as client:
        trends_client = client.app.state.trends_client

    assert isinstance(trends_client, MockTrendsClient)
    assert trends_client.stable is False


def test_live_mode_builds_live_client(tmp_path):
    """Live modda geçerli cookie dosyasıyla live client oluşturulur; açılışta ağa çıkılmaz."""
    cookie_file = tmp_path / "cookies.json"
    cookie_file.write_text(json.dumps([{"name": "SID", "value": "sahte"}]), encoding="utf-8")
    settings = Settings(_env_file=None, TRENDS_MODE="live", COOKIE_FILE=str(cookie_file), DATABASE_URL="sqlite://")

    with TestClient(create_app(settings)) as client:
        trends_client = client.app.state.trends_client

    assert isinstance(trends_client, LiveTrendsClient)
    assert trends_client.session.headers["Cookie"] == "SID=sahte"


def test_live_mode_without_cookie_fails_fast(tmp_path):
    """Live modda cookie dosyası yoksa uygulama açık bir mesajla başlamaz."""
    settings = Settings(
        _env_file=None, TRENDS_MODE="live", COOKIE_FILE=str(tmp_path / "yok.json"), DATABASE_URL="sqlite://"
    )

    with pytest.raises(RuntimeError) as error:
        with TestClient(create_app(settings)):
            pass

    message = str(error.value)
    assert "TRENDS_MODE=live ile başlatılamadı" in message
    assert "Cookie dosyası bulunamadı" in message
    assert "TRENDS_MODE=mock" in message


def test_live_mode_with_invalid_cookie_fails_fast(tmp_path):
    """Cookie dosyası kullanılabilir cookie içermiyorsa da uygulama başlamaz."""
    cookie_file = tmp_path / "cookies.json"
    cookie_file.write_text("[]", encoding="utf-8")
    settings = Settings(_env_file=None, TRENDS_MODE="live", COOKIE_FILE=str(cookie_file))

    with pytest.raises(RuntimeError, match="boş veya geçersiz"):
        build_trends_client(settings)


@pytest.mark.parametrize("content", ["", "bu json değil", '{"name": "SID", "value": "x"}', '["SID", 42]'])
def test_live_mode_with_malformed_cookie_file_fails_fast(tmp_path, content):
    """Boş, JSON olmayan veya cookie listesi içermeyen dosya anlaşılır bir mesajla reddedilir."""
    cookie_file = tmp_path / "cookies.json"
    cookie_file.write_text(content, encoding="utf-8")
    settings = Settings(_env_file=None, TRENDS_MODE="live", COOKIE_FILE=str(cookie_file))

    with pytest.raises(RuntimeError, match="boş veya geçersiz format") as error:
        build_trends_client(settings)

    # Dosyanın içeriği hata mesajına sızmaz
    assert content == "" or content not in str(error.value)


def test_live_mode_with_directory_instead_of_cookie_file_fails_fast(tmp_path):
    """Cookie yolu bir dizinse (yanlış bağlanmış volume) uygulama açık bir mesajla başlamaz."""
    cookie_dir = tmp_path / "cookies.json"
    cookie_dir.mkdir()
    settings = Settings(_env_file=None, TRENDS_MODE="live", COOKIE_FILE=str(cookie_dir))

    with pytest.raises(RuntimeError, match="TRENDS_MODE=live ile başlatılamadı"):
        build_trends_client(settings)


def test_injected_client_is_used(client, trends_client):
    """create_app'e verilen client aynen kullanılır (testler stable mock enjekte eder)."""
    assert client.app.state.trends_client is trends_client
    assert trends_client.stable is True
