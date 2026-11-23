# Endpoint'lere enjekte edilen ortak bağımlılıklar
# Değerler uygulama nesnesinden (app.state) okunur; böylece testler kendi ayar, veritabanı ve client'ını verebilir

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlmodel import Session

from app.core.config import Settings
from app.services.trends.base import TrendsClient


def get_settings(request: Request) -> Settings:
    """Uygulama oluşturulurken verilen ayarları döner."""
    return request.app.state.settings


def get_session(request: Request) -> Iterator[Session]:
    """İstek süresince açık kalan, istek bitince kapanan bir veritabanı oturumu verir."""
    # Oturumu aç, endpoint'e ver, iş bitince kapat
    with Session(request.app.state.engine) as session:
        yield session
    # Oturum kapatıldı


def get_trends_client(request: Request) -> TrendsClient:
    """Açılışta seçilen (mock veya live) trends client'ı döner."""
    return request.app.state.trends_client


# Endpoint imzalarını kısa tutan tip takma adları
SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[Session, Depends(get_session)]
TrendsClientDep = Annotated[TrendsClient, Depends(get_trends_client)]
