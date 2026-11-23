# Veritabanı engine'inin oluşturulması ve tabloların hazırlanması

from sqlalchemy import Engine
from sqlalchemy.engine import make_url
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

# Tabloların metadata'ya kaydolması için modeller import edilir
from app.db import models  # noqa: F401

# SQLite'ın dosyasız (bellek içi) veritabanı adları
SQLITE_BACKEND = "sqlite"
SQLITE_MEMORY_DATABASES = (None, "", ":memory:")


def create_db_engine(url: str) -> Engine:
    """Verilen adres için engine oluşturur; SQLite'a özgü ayarları kendisi ekler."""
    # SQLite dışındaki veritabanları (PostgreSQL) ek ayar gerektirmez
    parsed = make_url(url)
    if parsed.get_backend_name() != SQLITE_BACKEND:
        return create_engine(url)
    # Veritabanı türü belirlendi

    # Arka plan worker'ı ayrı bir thread'de çalıştığı için SQLite'ın thread kontrolü kapatılır
    connect_args = {"check_same_thread": False}
    # Bağlantı ayarları hazır

    # Bellek içi veritabanı bağlantı başına ayrıdır; tüm thread'ler aynı veriyi görsün diye
    # tek bağlantı paylaşılır (testlerde kullanılır)
    if parsed.database in SQLITE_MEMORY_DATABASES:
        return create_engine(url, connect_args=connect_args, poolclass=StaticPool)
    return create_engine(url, connect_args=connect_args)


def init_db(engine: Engine) -> None:
    """Eksik tabloları oluşturur; var olan tablolara dokunmaz."""
    SQLModel.metadata.create_all(engine)
