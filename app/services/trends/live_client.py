# Google Trends arka uç API'lerine HTTP istekleri atan gerçek client
# Cookie tabanlı oturum, explore + related searches çağrıları, retry/backoff

import json
import logging
import random
import time
from http import HTTPStatus
from pathlib import Path

import requests

from app.core.config import Settings
from app.services.trends.base import (
    EmptyTrendsError,
    KeywordTrends,
    TokenNotFoundError,
    TrendItem,
    TrendsError,
    normalize_keyword,
)

logger = logging.getLogger(__name__)

# HTTP header tanımları — tarayıcıyı taklit eder
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

BASE_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://trends.google.com/",
}
# HTTP header'ları tanımlandı

# Google Trends API endpoint'leri
EXPLORE_URL = "https://trends.google.com/trends/api/explore"
RELATED_SEARCHES_URL = "https://trends.google.com/trends/api/widgetdata/relatedsearches"
# Endpoint URL'leri tanımlandı

# Yanıt biçimine ait sabitler
XSSI_PREFIX = ")]}'"                      # Google yanıtlarının başındaki koruma öneki
RELATED_QUERIES_WIDGET_ID = "RELATED_QUERIES"
TOP_LIST_INDEX = 0                        # rankedList[0] = top
RISING_LIST_INDEX = 1                     # rankedList[1] = rising
# Yanıt sabitleri tanımlandı

# Her başarısız denemede bekleme süresi bu katsayıyla büyür (30, 60, 120)
BACKOFF_FACTOR = 2


# --- Cookie yönetimi ---

def load_cookies(path: str) -> str:
    """Cookie dosyasını okuyup 'name=val; name=val; ...' header string'ine çevirir."""
    # Dosya varlığı kontrolü; yoksa anlamlı hata
    cookie_path = Path(path)
    if not cookie_path.exists():
        raise FileNotFoundError(
            f"Cookie dosyası bulunamadı: {path}. "
            "EditThisCookie / Cookie-Editor ile export edip proje köküne koy."
        )
    # Dosya kontrolü tamamlandı

    # JSON oku ve cookie listesini header string'e dönüştür
    with cookie_path.open("r", encoding="utf-8") as f:
        cookies = json.load(f)
    cookie_pairs = [f"{c['name']}={c['value']}" for c in cookies if "name" in c and "value" in c]
    if not cookie_pairs:
        raise ValueError(f"Cookie dosyası boş veya geçersiz format: {path}")
    # Cookie string üretimi tamamlandı
    return "; ".join(cookie_pairs)


def build_session(cookie_header: str) -> requests.Session:
    """BASE_HEADERS + Cookie header bağlı requests.Session döner."""
    # Session ve header'ları hazırla
    session = requests.Session()
    session.headers.update(BASE_HEADERS)
    session.headers["Cookie"] = cookie_header
    # Session hazırlığı tamamlandı
    return session


# --- JSON temizleme ve ayrıştırma ---

def _clean_json(text: str):
    """Google Trends yanıtları )]}' veya )]}', öneki ile başlar; öneki at ve parse et."""
    # XSSI önekini ve ardından gelebilen virgülü ayıkla
    stripped = text.lstrip()
    if stripped.startswith(XSSI_PREFIX):
        stripped = stripped[len(XSSI_PREFIX):].lstrip(",")
    # Önek temizlendi
    return json.loads(stripped)


def _extract_keywords(ranked_lists: list, index: int) -> list[TrendItem]:
    """rankedList[index].rankedKeyword içinden TrendItem listesi üretir."""
    # İndex sınırı kontrolü
    if len(ranked_lists) <= index:
        return []
    items = ranked_lists[index].get("rankedKeyword", [])
    # Liste mevcut

    # Sorgu metni veya sayısal değeri olmayan bozuk kayıtlar atlanır
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        query = item.get("query", "")
        try:
            value = int(item.get("value"))
        except (TypeError, ValueError):
            continue
        if not query:
            continue
        result.append(TrendItem(query=query, value=value, formatted_value=str(item.get("formattedValue", ""))))
    # TrendItem listesi hazır
    return result


# --- Client ---

class LiveTrendsClient:
    """Gerçek Google Trends'e cookie oturumuyla istek atan client."""

    def __init__(self, settings: Settings):
        """Cookie dosyasını okuyup oturumu kurar; dosya yoksa veya geçersizse hata fırlatır."""
        self.settings = settings
        self.session = build_session(load_cookies(settings.COOKIE_FILE))

    # --- Explore endpoint (token üretici) ---

    def get_explore_payload(self, keyword: str, geo: str, timeframe: str) -> tuple[str | None, dict | None]:
        """Explore endpoint'i çağırır, related queries widget'ının token + request'ini döner."""
        # Keyword'ü lowercase gönder — Google case-sensitive davranıyor,
        # "Camera Bags" boş dönüyor "camera bags" doluyor
        keyword_normalized = normalize_keyword(keyword)
        # Keyword normalize edildi

        # Explore request gövdesini hazırla
        req_body = {
            "comparisonItem": [{"keyword": keyword_normalized, "geo": geo, "time": timeframe}],
            "category": 0,
            "property": "",
        }
        params = {
            "hl": self.settings.HL,
            "tz": self.settings.TZ_OFFSET,
            "req": json.dumps(req_body, separators=(",", ":")),
        }
        # Explore parametreleri hazır

        # İsteği at ve doğrula
        resp = self.session.get(EXPLORE_URL, params=params, timeout=self.settings.REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = _clean_json(resp.text)
        # Yanıt JSON olarak parse edildi

        # Widget listesinden RELATED_QUERIES olanı bul; yoksa (None, None) dönülür
        widgets = data.get("widgets", []) if isinstance(data, dict) else []
        for widget in widgets:
            if isinstance(widget, dict) and widget.get("id", "") == RELATED_QUERIES_WIDGET_ID:
                token = widget.get("token")
                req = widget.get("request")
                if token and req:
                    return token, req
        return None, None

    # --- Related searches endpoint (asıl veri) ---

    def get_related_searches(self, token: str, req: dict) -> KeywordTrends:
        """Token ile related searches endpoint'i çağırır; top ve rising listelerini döner."""
        # Request parametrelerini hazırla
        params = {
            "hl": self.settings.HL,
            "tz": self.settings.TZ_OFFSET,
            "req": json.dumps(req, separators=(",", ":")),
            "token": token,
        }
        # Parametreler hazır

        # İsteği at ve parse et
        resp = self.session.get(RELATED_SEARCHES_URL, params=params, timeout=self.settings.REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = _clean_json(resp.text)
        if not isinstance(data, dict):
            raise TrendsError("Beklenmeyen yanıt biçimi")
        # Yanıt parse edildi

        # rankedList[0] = top, rankedList[1] = rising — her ikisini de güvenli şekilde çek
        ranked_lists = data.get("default", {}).get("rankedList", [])
        return KeywordTrends(
            top=_extract_keywords(ranked_lists, TOP_LIST_INDEX),
            rising=_extract_keywords(ranked_lists, RISING_LIST_INDEX),
        )

    # --- Tek keyword için tam akış + retry/backoff ---

    def fetch_keyword(self, keyword: str, geo: str, timeframe: str) -> KeywordTrends:
        """Tek keyword için explore → related searches akışı; 429 ve bağlantı hatalarında exponential backoff."""
        max_retries = self.settings.MAX_RETRIES
        last_error = ""
        for attempt in range(max_retries):
            try:
                # Explore ile token al
                token, req = self.get_explore_payload(keyword, geo, timeframe)
                if not token or not req:
                    raise TokenNotFoundError("Related queries widget token bulunamadı")
                # Token alındı

                # İstekler arası kısa bekleme (Google insanlığı sever)
                self._sleep_jittered()

                # Related searches'i çek
                trends = self.get_related_searches(token, req)
                if not trends.top and not trends.rising:
                    raise EmptyTrendsError("Boş veri döndü (top ve rising her ikisi de boş)")
                return trends

            except requests.HTTPError as e:
                # Yalnızca 429 tekrar denenir; diğer HTTP hataları direkt fırlatılır
                status_code = e.response.status_code if e.response is not None else None
                if status_code != HTTPStatus.TOO_MANY_REQUESTS:
                    raise TrendsError(f"HTTP {status_code}") from e
                last_error = f"HTTP {status_code}"

            except (requests.ConnectionError, requests.Timeout) as e:
                # Geçici ağ sorunları da tekrar denenir
                last_error = type(e).__name__

            except requests.RequestException as e:
                # Diğer istek hataları — retry'a değmez
                raise TrendsError(f"İstek başarısız: {type(e).__name__}") from e

            except ValueError as e:
                # JSON yerine başka içerik geldi (ör. cookie süresi dolunca giriş sayfası)
                raise TrendsError("Yanıt JSON olarak ayrıştırılamadı (cookie süresi dolmuş olabilir)") from e

            # Son deneme değilse giderek artan süre bekle
            if attempt < max_retries - 1:
                wait = self.settings.BACKOFF_BASE * (BACKOFF_FACTOR ** attempt)
                logger.warning(
                    "%s alındı, %ss bekleniyor (deneme %s/%s)", last_error, wait, attempt + 1, max_retries
                )
                time.sleep(wait)
            # Backoff adımı tamamlandı

        # Tüm denemeler tükendi
        raise TrendsError(f"{max_retries} denemede başarısız: {last_error}")

    # --- Bekleme ---

    def pause_between_keywords(self) -> None:
        """İki keyword arasında rastgele süre bekler."""
        self._sleep_jittered()

    def _sleep_jittered(self) -> None:
        """SLEEP_MIN ile SLEEP_MAX arasında rastgele saniye bekler."""
        time.sleep(random.uniform(self.settings.SLEEP_MIN, self.settings.SLEEP_MAX))
