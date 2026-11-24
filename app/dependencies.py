# Endpoint'lere enjekte edilen ortak bağımlılıklar
# Değerler uygulama nesnesinden (app.state) okunur; böylece testler kendi ayar, veritabanı ve client'ını verebilir

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Query, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy import Engine
from sqlmodel import Session

from app.core.config import Settings
from app.services.trends.base import TrendsClient


def get_settings(request: Request) -> Settings:
    """Uygulama oluşturulurken verilen ayarları döner."""
    return request.app.state.settings


def get_engine(request: Request) -> Engine:
    """Açılışta oluşturulan veritabanı engine'ini döner (arka plan işleri kendi oturumunu bununla açar)."""
    return request.app.state.engine


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
EngineDep = Annotated[Engine, Depends(get_engine)]
SessionDep = Annotated[Session, Depends(get_session)]
TrendsClientDep = Annotated[TrendsClient, Depends(get_trends_client)]


# --- Doğrulama hataları ---

# Pydantic'in kendi hata türleriyle aynı biçimde raporlanan, iş kuralı kaynaklı hata türü
VALUE_ERROR_TYPE = "value_error"


def validation_error(location: str, field: str, message: str) -> RequestValidationError:
    """Ayarlara bağlı bir sınır aşıldığında fırlatılacak 422 hatasını üretir.

    Pydantic'in şema hatalarıyla aynı biçimde döner; böylece istemci tüm 422 yanıtlarını
    tek bir biçimde ele alır: {"detail": [{"loc": ["query", "limit"], "msg": "...", "type": "..."}]}
    """
    return RequestValidationError([{"type": VALUE_ERROR_TYPE, "loc": (location, field), "msg": message}])


# --- Sayfalama ---

@dataclass
class Pagination:
    """Listeleme endpoint'lerinin ortak sayfalama parametreleri."""

    limit: int
    offset: int


def get_pagination(
    settings: SettingsDep,
    limit: Annotated[
        int | None,
        Query(ge=1, description="Sayfa boyutu. Verilmezse DEFAULT_PAGE_SIZE, en fazla MAX_PAGE_SIZE."),
    ] = None,
    offset: Annotated[int, Query(ge=0, description="Baştan atlanacak kayıt sayısı.")] = 0,
) -> Pagination:
    """limit ve offset parametrelerini okur; sınırlar ayarlardan gelir."""
    # Verilmeyen limit için varsayılanı kullan, üst sınırı aşanı reddet
    if limit is None:
        limit = settings.DEFAULT_PAGE_SIZE
    if limit > settings.MAX_PAGE_SIZE:
        raise validation_error("query", "limit", f"limit can be at most {settings.MAX_PAGE_SIZE}")
    # Sayfalama parametreleri hazır
    return Pagination(limit=limit, offset=offset)


PaginationDep = Annotated[Pagination, Depends(get_pagination)]
