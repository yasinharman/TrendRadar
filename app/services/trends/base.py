# Trends client'larının ortak sözleşmesi: veri tipleri, hata sınıfları ve arayüz
# Live ve mock client aynı arayüzü uygular; uygulamanın geri kalanı hangisinin çalıştığını bilmez

from dataclasses import dataclass
from typing import Protocol


# --- Veri tipleri ---

@dataclass
class TrendItem:
    """Google Trends'in döndürdüğü tek bir ilişkili arama."""

    query: str
    value: int
    formatted_value: str  # Google'ın formattedValue alanı (ör. "Breakout", "+800%")


@dataclass
class KeywordTrends:
    """Bir seed keyword için en çok aranan ve yükselişteki aramalar."""

    top: list[TrendItem]
    rising: list[TrendItem]


# --- Hata sınıfları ---

class TrendsError(Exception):
    """Bir keyword'ün taranamadığını bildiren genel hata."""


class EmptyTrendsError(TrendsError):
    """Google yanıt verdi ama top ve rising listelerinin ikisi de boş."""


class TokenNotFoundError(TrendsError):
    """Explore yanıtında related queries widget token'ı bulunamadı."""


# --- Client arayüzü ---

class TrendsClient(Protocol):
    """Job runner'ın bir trends client'tan beklediği metotlar."""

    def fetch_keyword(self, keyword: str, geo: str, timeframe: str) -> KeywordTrends:
        """Tek keyword için top ve rising aramaları getirir; başarısızlıkta TrendsError fırlatır."""
        ...

    def pause_between_keywords(self) -> None:
        """İki keyword arasında gereken beklemeyi yapar."""
        ...


# --- Ortak yardımcılar ---

def normalize_keyword(keyword: str) -> str:
    """Keyword'ü karşılaştırma için sadeleştirir: kenar boşlukları atılır, boşluklar teke iner, küçük harf olur."""
    return " ".join(keyword.split()).lower()


def is_breakout(value: int, threshold: int) -> bool:
    """Rising değerinin breakout sayılıp sayılmadığını söyler.

    Karar yalnızca sayısal değere göre verilir; Google'ın formattedValue metni dile göre
    değiştiği için (hl=tr-TR ile Türkçe gelir) ona güvenilmez.
    """
    return value >= threshold
