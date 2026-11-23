# Live client'ın ayrıştırma ve retry mantığı testleri
# Hiçbir test ağa çıkmaz: requests.Session sahte bir nesneyle değiştirilir, time.sleep kaydedilir

import json
from pathlib import Path

import pytest
import requests

from app.core.config import Settings
from app.services.trends import live_client
from app.services.trends.base import (
    EmptyTrendsError,
    TokenNotFoundError,
    TrendItem,
    TrendsError,
    is_breakout,
)
from app.services.trends.live_client import (
    EXPLORE_URL,
    RELATED_SEARCHES_URL,
    LiveTrendsClient,
    _clean_json,
    _extract_keywords,
    build_session,
    load_cookies,
)

FIXTURES = Path(__file__).parent / "fixtures"
EXPLORE_TEXT = (FIXTURES / "explore_response.txt").read_text(encoding="utf-8")
RELATED_TEXT = (FIXTURES / "related_searches_response.txt").read_text(encoding="utf-8")

# Fixture'lardaki sahte token
FAKE_TOKEN = "FAKE_TOKEN_RELATED_QUERIES"


# --- Sahte HTTP katmanı ---

class FakeResponse:
    """requests.Response'un testlerde kullanılan kısmını taklit eder."""

    def __init__(self, text: str = "", status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Error", response=self)


class FakeSession:
    """Sırayla önceden verilen yanıtları döner ve yapılan çağrıları kaydeder."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def wrap(data) -> str:
    """Bir Python nesnesini Google'ın XSSI önekli yanıt biçimine çevirir."""
    return ")]}',\n" + json.dumps(data)


# --- Fixture'lar ---

@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    """time.sleep'i etkisizleştirir ve istenen bekleme sürelerini kaydeder."""
    recorded: list[float] = []
    monkeypatch.setattr(live_client.time, "sleep", recorded.append)
    return recorded


@pytest.fixture
def live_settings(tmp_path) -> Settings:
    """Sahte cookie dosyası kullanan, jitter beklemesi sıfır olan live ayarları."""
    cookie_file = tmp_path / "cookies.json"
    cookie_file.write_text(json.dumps([{"name": "SID", "value": "sahte"}]), encoding="utf-8")
    return Settings(_env_file=None, TRENDS_MODE="live", COOKIE_FILE=str(cookie_file), SLEEP_MIN=0, SLEEP_MAX=0)


def make_client(settings: Settings, responses) -> tuple[LiveTrendsClient, FakeSession]:
    """Gerçek oturumu sahte oturumla değiştirilmiş bir client kurar."""
    client = LiveTrendsClient(settings)
    session = FakeSession(responses)
    client.session = session
    return client, session


def backoff_waits(sleeps: list[float]) -> list[float]:
    """Sıfır süreli jitter beklemelerini ayıklayıp yalnızca backoff beklemelerini döner."""
    return [seconds for seconds in sleeps if seconds > 0]


# --- _clean_json: XSSI önek temizleme ---

@pytest.mark.parametrize(
    "text",
    [
        ")]}',\n{\"a\": 1}",   # relatedsearches biçimi
        ")]}'\n{\"a\": 1}",    # explore biçimi
        ")]}',{\"a\": 1}",     # satır sonu olmadan
        "  \n)]}'\n{\"a\": 1}",  # baştaki boşluklarla
        "{\"a\": 1}",          # öneksiz
    ],
)
def test_clean_json_strips_xssi_prefix(text):
    """Önekin tüm biçimleri temizlenir, öneksiz JSON da kabul edilir."""
    assert _clean_json(text) == {"a": 1}


def test_clean_json_parses_fixtures():
    """Fixture dosyaları önekleriyle birlikte ayrıştırılabilir."""
    assert len(_clean_json(EXPLORE_TEXT)["widgets"]) == 4
    assert len(_clean_json(RELATED_TEXT)["default"]["rankedList"]) == 2


def test_clean_json_rejects_non_json():
    """JSON olmayan içerik (ör. giriş sayfası HTML'i) ValueError fırlatır."""
    with pytest.raises(ValueError):
        _clean_json("<html><body>Sign in</body></html>")


# --- _extract_keywords: top/rising ayrıştırma ---

def test_extract_keywords_parses_top_and_rising():
    """rankedList[0] top, rankedList[1] rising olarak TrendItem'lara çevrilir."""
    ranked_lists = _clean_json(RELATED_TEXT)["default"]["rankedList"]

    top = _extract_keywords(ranked_lists, 0)
    rising = _extract_keywords(ranked_lists, 1)

    assert top[0] == TrendItem(query="ornek urun", value=100, formatted_value="100")
    assert [item.value for item in top] == [100, 64, 37, 12]
    assert rising[0] == TrendItem(query="ornek urun kampanya", value=26550, formatted_value="Aşırı yükselme")
    assert [item.value for item in rising] == [26550, 5000, 4950, 800]
    assert all(isinstance(item.value, int) for item in top + rising)


def test_extract_keywords_handles_missing_lists():
    """Eksik rankedList veya rankedKeyword boş liste döner, hata vermez."""
    assert _extract_keywords([], 0) == []
    assert _extract_keywords([{"rankedKeyword": [{"query": "a", "value": 1}]}], 1) == []
    assert _extract_keywords([{}], 0) == []


def test_extract_keywords_skips_malformed_items():
    """Sorgusu veya sayısal değeri olmayan kayıtlar atlanır; sayı metin olarak gelirse çevrilir."""
    ranked_lists = [
        {
            "rankedKeyword": [
                {"query": "gecerli", "value": 40, "formattedValue": "40"},
                {"query": "metin sayi", "value": "25"},
                {"query": "degersiz"},
                {"query": "sayi degil", "value": "Breakout"},
                {"value": 10},
                "bozuk kayit",
            ]
        }
    ]

    items = _extract_keywords(ranked_lists, 0)

    assert items == [
        TrendItem(query="gecerli", value=40, formatted_value="40"),
        TrendItem(query="metin sayi", value=25, formatted_value=""),
    ]


# --- Breakout tespiti ---

def test_breakout_threshold_is_inclusive():
    """Eşiğe eşit ve üzerindeki değerler breakout, altındakiler değildir."""
    threshold = Settings(_env_file=None).BREAKOUT_THRESHOLD

    assert is_breakout(threshold, threshold) is True
    assert is_breakout(threshold + 1, threshold) is True
    assert is_breakout(threshold - 1, threshold) is False


def test_breakout_detection_ignores_localized_text():
    """Breakout kararı Türkçe gelen formattedValue metnine değil sayısal değere dayanır."""
    threshold = Settings(_env_file=None).BREAKOUT_THRESHOLD
    rising = _extract_keywords(_clean_json(RELATED_TEXT)["default"]["rankedList"], 1)

    flags = [is_breakout(item.value, threshold) for item in rising]

    assert "Breakout" not in {item.formatted_value for item in rising}
    assert flags == [True, True, False, False]


# --- Cookie ve oturum ---

def test_load_cookies_builds_header(tmp_path):
    """Cookie listesi tek bir header string'ine çevrilir; eksik alanlı kayıtlar atlanır."""
    cookie_file = tmp_path / "cookies.json"
    cookie_file.write_text(
        json.dumps([{"name": "SID", "value": "a1"}, {"name": "HSID", "value": "b2"}, {"domain": ".google.com"}]),
        encoding="utf-8",
    )

    assert load_cookies(str(cookie_file)) == "SID=a1; HSID=b2"


def test_load_cookies_missing_file(tmp_path):
    """Dosya yoksa anlamlı mesajla FileNotFoundError fırlatılır."""
    with pytest.raises(FileNotFoundError, match="Cookie dosyası bulunamadı"):
        load_cookies(str(tmp_path / "yok.json"))


def test_load_cookies_empty_file(tmp_path):
    """Kullanılabilir cookie içermeyen dosya ValueError fırlatır."""
    cookie_file = tmp_path / "cookies.json"
    cookie_file.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="boş veya geçersiz"):
        load_cookies(str(cookie_file))


def test_build_session_sets_headers():
    """Oturum tarayıcı header'larını ve cookie'yi taşır."""
    session = build_session("SID=a1")

    assert session.headers["Cookie"] == "SID=a1"
    assert session.headers["User-Agent"] == live_client.USER_AGENT
    assert session.headers["Referer"] == "https://trends.google.com/"


# --- fetch_keyword: tam akış ---

def test_fetch_keyword_success(live_settings, sleeps):
    """Explore → related searches akışı doğru parametrelerle çağrılır ve sonuç ayrıştırılır."""
    client, session = make_client(live_settings, [FakeResponse(EXPLORE_TEXT), FakeResponse(RELATED_TEXT)])

    trends = client.fetch_keyword("  Ornek   Urun ", geo="TR", timeframe="today 3-m")

    # Sonuç ayrıştırıldı
    assert [item.query for item in trends.top][:2] == ["ornek urun", "en iyi ornek urun"]
    assert len(trends.top) == 4
    assert len(trends.rising) == 4

    # Explore isteği: keyword lowercase ve sadeleştirilmiş, geo/timeframe parametreden
    explore_call, related_call = session.calls
    assert explore_call["url"] == EXPLORE_URL
    assert explore_call["timeout"] == live_settings.REQUEST_TIMEOUT
    assert explore_call["params"]["hl"] == "tr-TR"
    assert explore_call["params"]["tz"] == "-180"
    assert json.loads(explore_call["params"]["req"]) == {
        "comparisonItem": [{"keyword": "ornek urun", "geo": "TR", "time": "today 3-m"}],
        "category": 0,
        "property": "",
    }

    # Related searches isteği: explore'dan gelen token ve request aynen iletilir
    expected_request = next(w for w in _clean_json(EXPLORE_TEXT)["widgets"] if w["id"] == "RELATED_QUERIES")["request"]
    assert related_call["url"] == RELATED_SEARCHES_URL
    assert related_call["params"]["token"] == FAKE_TOKEN
    assert json.loads(related_call["params"]["req"]) == expected_request

    # İki istek arasında bir kez jitter beklemesi yapıldı, backoff yok
    assert len(sleeps) == 1
    assert backoff_waits(sleeps) == []


def test_fetch_keyword_only_top_list(live_settings, sleeps):
    """Yalnızca top listesi dönerse rising boş kalır ve hata sayılmaz."""
    data = _clean_json(RELATED_TEXT)
    data["default"]["rankedList"] = data["default"]["rankedList"][:1]
    client, _ = make_client(live_settings, [FakeResponse(EXPLORE_TEXT), FakeResponse(wrap(data))])

    trends = client.fetch_keyword("ornek urun", geo="", timeframe="today 1-m")

    assert len(trends.top) == 4
    assert trends.rising == []


def test_fetch_keyword_token_not_found(live_settings, sleeps):
    """Explore yanıtında related queries widget'ı yoksa TokenNotFoundError; tekrar denenmez."""
    data = _clean_json(EXPLORE_TEXT)
    data["widgets"] = [w for w in data["widgets"] if w["id"] != "RELATED_QUERIES"]
    client, session = make_client(live_settings, [FakeResponse(wrap(data))])

    with pytest.raises(TokenNotFoundError, match="token bulunamadı"):
        client.fetch_keyword("ornek urun", geo="", timeframe="today 1-m")

    assert len(session.calls) == 1
    assert sleeps == []


def test_fetch_keyword_empty_result(live_settings, sleeps):
    """Top ve rising ikisi de boşsa EmptyTrendsError; tekrar denenmez."""
    empty = {"default": {"rankedList": [{"rankedKeyword": []}, {"rankedKeyword": []}]}}
    client, session = make_client(live_settings, [FakeResponse(EXPLORE_TEXT), FakeResponse(wrap(empty))])

    with pytest.raises(EmptyTrendsError, match="Boş veri döndü"):
        client.fetch_keyword("ornek urun", geo="", timeframe="today 1-m")

    assert len(session.calls) == 2
    assert backoff_waits(sleeps) == []


def test_fetch_keyword_non_json_response(live_settings, sleeps):
    """JSON yerine HTML gelirse açıklayıcı bir TrendsError fırlatılır."""
    client, _ = make_client(live_settings, [FakeResponse("<html>Sign in</html>")])

    with pytest.raises(TrendsError, match="JSON olarak ayrıştırılamadı"):
        client.fetch_keyword("ornek urun", geo="", timeframe="today 1-m")


def test_fetch_keyword_unexpected_shape(live_settings, sleeps):
    """Related searches yanıtı beklenen yapıda değilse TrendsError fırlatılır."""
    client, _ = make_client(live_settings, [FakeResponse(EXPLORE_TEXT), FakeResponse(wrap(["liste"]))])

    with pytest.raises(TrendsError, match="Beklenmeyen yanıt biçimi"):
        client.fetch_keyword("ornek urun", geo="", timeframe="today 1-m")


# --- fetch_keyword: retry ve backoff ---

def test_429_is_retried_with_exponential_backoff(live_settings, sleeps):
    """429 sonrası 30 ve 60 saniye beklenip tekrar denenir; üçüncü deneme başarılı olur."""
    client, session = make_client(
        live_settings,
        [
            FakeResponse(status_code=429),
            FakeResponse(status_code=429),
            FakeResponse(EXPLORE_TEXT),
            FakeResponse(RELATED_TEXT),
        ],
    )

    trends = client.fetch_keyword("ornek urun", geo="", timeframe="today 1-m")

    assert len(trends.top) == 4
    assert len(session.calls) == 4
    assert backoff_waits(sleeps) == [30, 60]


def test_429_on_related_searches_restarts_flow(live_settings, sleeps):
    """429 ikinci istekte gelirse akış explore'dan yeniden başlar."""
    client, session = make_client(
        live_settings,
        [
            FakeResponse(EXPLORE_TEXT),
            FakeResponse(status_code=429),
            FakeResponse(EXPLORE_TEXT),
            FakeResponse(RELATED_TEXT),
        ],
    )

    client.fetch_keyword("ornek urun", geo="", timeframe="today 1-m")

    assert [call["url"] for call in session.calls] == [
        EXPLORE_URL, RELATED_SEARCHES_URL, EXPLORE_URL, RELATED_SEARCHES_URL,
    ]
    assert backoff_waits(sleeps) == [30]


def test_429_exhausts_retries(live_settings, sleeps):
    """Tüm denemeler 429 dönerse TrendsError fırlatılır; son denemeden sonra beklenmez."""
    client, session = make_client(live_settings, [FakeResponse(status_code=429)] * 3)

    with pytest.raises(TrendsError, match="3 denemede başarısız: HTTP 429"):
        client.fetch_keyword("ornek urun", geo="", timeframe="today 1-m")

    assert len(session.calls) == live_settings.MAX_RETRIES
    assert backoff_waits(sleeps) == [30, 60]


@pytest.mark.parametrize("error", [requests.ConnectionError("bağlantı koptu"), requests.Timeout("zaman aşımı")])
def test_network_errors_are_retried(live_settings, sleeps, error):
    """Bağlantı hatası ve zaman aşımı da backoff ile tekrar denenir."""
    client, session = make_client(live_settings, [error, FakeResponse(EXPLORE_TEXT), FakeResponse(RELATED_TEXT)])

    trends = client.fetch_keyword("ornek urun", geo="", timeframe="today 1-m")

    assert len(trends.rising) == 4
    assert len(session.calls) == 3
    assert backoff_waits(sleeps) == [30]


def test_network_errors_exhaust_retries(live_settings, sleeps):
    """Bağlantı hiç kurulamazsa hata türünü içeren TrendsError fırlatılır."""
    client, _ = make_client(live_settings, [requests.ConnectionError("bağlantı koptu")] * 3)

    with pytest.raises(TrendsError, match="3 denemede başarısız: ConnectionError"):
        client.fetch_keyword("ornek urun", geo="", timeframe="today 1-m")


def test_other_http_errors_are_not_retried(live_settings, sleeps):
    """429 dışındaki HTTP hataları beklemeden TrendsError'a çevrilir."""
    client, session = make_client(live_settings, [FakeResponse(status_code=500)])

    with pytest.raises(TrendsError, match="HTTP 500"):
        client.fetch_keyword("ornek urun", geo="", timeframe="today 1-m")

    assert len(session.calls) == 1
    assert sleeps == []


def test_other_request_errors_are_not_retried(live_settings, sleeps):
    """Geçici olmayan istek hataları (ör. yönlendirme döngüsü) beklemeden TrendsError'a çevrilir."""
    client, session = make_client(live_settings, [requests.TooManyRedirects("döngü")])

    with pytest.raises(TrendsError, match="İstek başarısız: TooManyRedirects"):
        client.fetch_keyword("ornek urun", geo="", timeframe="today 1-m")

    assert len(session.calls) == 1
    assert sleeps == []


def test_backoff_uses_settings(live_settings, sleeps):
    """Deneme sayısı ve bekleme tabanı ayarlardan okunur."""
    settings = live_settings.model_copy(update={"MAX_RETRIES": 4, "BACKOFF_BASE": 1})
    client, session = make_client(settings, [FakeResponse(status_code=429)] * 4)

    with pytest.raises(TrendsError, match="4 denemede başarısız"):
        client.fetch_keyword("ornek urun", geo="", timeframe="today 1-m")

    assert len(session.calls) == 4
    assert backoff_waits(sleeps) == [1, 2, 4]


# --- Keyword'ler arası bekleme ---

def test_pause_between_keywords_sleeps_within_range(live_settings, sleeps):
    """Bekleme süresi SLEEP_MIN ile SLEEP_MAX arasında seçilir."""
    settings = live_settings.model_copy(update={"SLEEP_MIN": 5, "SLEEP_MAX": 12})
    client, _ = make_client(settings, [])

    client.pause_between_keywords()

    assert len(sleeps) == 1
    assert 5 <= sleeps[0] <= 12
