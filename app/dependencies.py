# Endpoint'lere enjekte edilen ortak bağımlılıklar
# Değerler uygulama nesnesinden (app.state) okunur; böylece testler kendi ayar ve veritabanını verebilir

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlmodel import Session

from app.core.config import Settings


def get_settings(request: Request) -> Settings:
    """Uygulama oluşturulurken verilen ayarları döner."""
    return request.app.state.settings


def get_session(request: Request) -> Iterator[Session]:
    """İstek süresince açık kalan, istek bitince kapanan bir veritabanı oturumu verir."""
    # Oturumu aç, endpoint'e ver, iş bitince kapat
    with Session(request.app.state.engine) as session:
        yield session
    # Oturum kapatıldı


# Endpoint imzalarını kısa tutan tip takma adları
SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[Session, Depends(get_session)]
