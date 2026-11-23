# Testlerin paylaştığı fixture'lar
# Testler asla gerçek Google'a istek atmaz ve diskteki veritabanına dokunmaz

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import Session

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    """Mock mod, bellek içi SQLite ve beklemesiz ayarlar; geliştiricinin `.env` dosyası okunmaz."""
    return Settings(_env_file=None, TRENDS_MODE="mock", DATABASE_URL="sqlite://", MOCK_DELAY_SECONDS=0)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    """Test ayarlarıyla kurulmuş uygulamaya istek atan istemci; `with` bloğu lifespan'i çalıştırır."""
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def engine(client: TestClient) -> Engine:
    """Uygulamanın kullandığı engine; testler veritabanını doğrudan hazırlayıp inceleyebilir."""
    return client.app.state.engine


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """Uygulamayla aynı veritabanına bağlı oturum."""
    with Session(engine) as db_session:
        yield db_session
