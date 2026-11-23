# Settings sınıfının varsayılanları, ortam değişkeni okuması ve doğrulamaları

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings

# Plan bölüm 5'teki varsayılan değerler
EXPECTED_DEFAULTS = {
    "APP_NAME": "TrendRadar API",
    "TRENDS_MODE": "mock",
    "DATABASE_URL": "sqlite:///./trendradar.db",
    "COOKIE_FILE": "cookies.json",
    "HL": "tr-TR",
    "TZ_OFFSET": "-180",
    "DEFAULT_GEO": "",
    "DEFAULT_TIMEFRAME": "today 1-m",
    "SLEEP_MIN": 5,
    "SLEEP_MAX": 12,
    "MAX_RETRIES": 3,
    "BACKOFF_BASE": 30,
    "REQUEST_TIMEOUT": 30,
    "BREAKOUT_THRESHOLD": 5000,
    "MAX_KEYWORDS_PER_JOB": 30,
    "MAX_KEYWORD_LENGTH": 100,
    "DEFAULT_PAGE_SIZE": 50,
    "MAX_PAGE_SIZE": 200,
    "MOCK_DELAY_SECONDS": 0.5,
}


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    """Geliştiricinin ortam değişkenleri test sonucunu etkilemesin."""
    for name in EXPECTED_DEFAULTS:
        monkeypatch.delenv(name, raising=False)


def test_defaults_match_plan():
    """Hiçbir şey verilmezse plandaki varsayılanlar kullanılır."""
    settings = Settings(_env_file=None)

    assert settings.model_dump() == EXPECTED_DEFAULTS


def test_environment_variables_override_defaults(monkeypatch):
    """Ortam değişkenleri varsayılanların önüne geçer ve doğru tipe çevrilir."""
    monkeypatch.setenv("TRENDS_MODE", "live")
    monkeypatch.setenv("BREAKOUT_THRESHOLD", "9000")

    settings = Settings(_env_file=None)

    assert settings.TRENDS_MODE == "live"
    assert settings.BREAKOUT_THRESHOLD == 9000


def test_env_file_is_read(tmp_path):
    """Değerler `.env` dosyasından okunur."""
    env_file = tmp_path / ".env"
    env_file.write_text('APP_NAME="Deneme API"\nDEFAULT_GEO=TR\n', encoding="utf-8")

    settings = Settings(_env_file=env_file)

    assert settings.APP_NAME == "Deneme API"
    assert settings.DEFAULT_GEO == "TR"


@pytest.mark.parametrize(
    "overrides",
    [
        {"TRENDS_MODE": "production"},
        {"DEFAULT_TIMEFRAME": "today 7-d"},
        {"DEFAULT_GEO": "tr"},
        {"DEFAULT_GEO": "TUR"},
        {"MAX_RETRIES": 0},
        {"SLEEP_MIN": 20, "SLEEP_MAX": 10},
        {"DEFAULT_PAGE_SIZE": 500, "MAX_PAGE_SIZE": 200},
    ],
)
def test_invalid_values_are_rejected(overrides):
    """Geçersiz veya birbiriyle çelişen ayarlar uygulama açılmadan reddedilir."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **overrides)


def test_get_settings_is_cached():
    """get_settings her çağrıda aynı nesneyi döner."""
    get_settings.cache_clear()

    assert get_settings() is get_settings()

    get_settings.cache_clear()
