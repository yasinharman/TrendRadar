# Mock client testleri: deterministiklik, veri biçimi, hata senaryoları ve tekrar taramalardaki varyasyon

import pytest

from app.core.config import Settings
from app.services.trends import mock_client
from app.services.trends.base import EmptyTrendsError, KeywordTrends, TrendsError, is_breakout
from app.services.trends.mock_client import (
    RISING_COUNT_MAX,
    RISING_COUNT_MIN,
    TOP_COUNT_MAX,
    TOP_COUNT_MIN,
    TOP_VALUE_MAX,
    VARIATION_RATIO,
    MockTrendsClient,
)

# Eski scraper'ın seed listesinden örnekler
SAMPLE_KEYWORDS = ["Camera Bags", "Backpacks", "Tote Bags", "Wine Carriers", "Passport Holders", "Mouse Pads"]

# Yeniden sıralama sonrası yuvarlama payı (rising değerleri 10'a yuvarlanır)
ROUNDING_TOLERANCE = 10


def fetch(client: MockTrendsClient, keyword: str) -> KeywordTrends:
    """Varsayılan geo ve timeframe ile tek keyword çeker."""
    return client.fetch_keyword(keyword, geo="", timeframe="today 1-m")


def assert_valid_shape(trends: KeywordTrends, keyword: str, threshold: int) -> None:
    """Sonucun plandaki kurallara uyduğunu doğrular."""
    top_values = [item.value for item in trends.top]
    rising_values = [item.value for item in trends.rising]
    queries = [item.query for item in trends.top + trends.rising]

    # top: 5–10 sonuç, ilki 100, azalan değerler
    assert TOP_COUNT_MIN <= len(trends.top) <= TOP_COUNT_MAX
    assert top_values[0] == TOP_VALUE_MAX
    assert top_values == sorted(top_values, reverse=True)
    assert all(1 <= value <= TOP_VALUE_MAX for value in top_values)

    # rising: 3–8 sonuç, büyükten küçüğe, en az biri breakout
    assert RISING_COUNT_MIN <= len(trends.rising) <= RISING_COUNT_MAX
    assert rising_values == sorted(rising_values, reverse=True)
    assert any(is_breakout(value, threshold) for value in rising_values)

    # Sorgular benzersiz ve keyword'ü içeriyor
    assert len(queries) == len(set(queries))
    assert all(keyword.lower() in query for query in queries)


# --- Deterministiklik ---

@pytest.mark.parametrize("keyword", SAMPLE_KEYWORDS)
def test_stable_client_returns_valid_and_identical_results(settings, keyword):
    """stable=True iken aynı keyword her çağrıda ve her client'ta birebir aynı sonucu verir."""
    client = MockTrendsClient(settings, stable=True)

    first = fetch(client, keyword)
    second = fetch(client, keyword)
    from_other_client = fetch(MockTrendsClient(settings, stable=True), keyword)

    assert_valid_shape(first, keyword, settings.BREAKOUT_THRESHOLD)
    assert first == second == from_other_client


def test_result_does_not_depend_on_case_or_spacing(settings):
    """Seed normalize edilmiş keyword'den türetilir."""
    client = MockTrendsClient(settings, stable=True)

    assert fetch(client, "Camera Bags") == fetch(client, "  camera   BAGS ")


def test_different_keywords_give_different_results(settings):
    """Farklı keyword'ler farklı liste üretir."""
    client = MockTrendsClient(settings, stable=True)

    results = [[item.value for item in fetch(client, keyword).top] for keyword in SAMPLE_KEYWORDS]

    assert len({tuple(values) for values in results}) > 1


def test_seed_is_stable_across_processes():
    """Seed, her süreçte değişen hash() yerine SHA-256'dan gelir; bu yüzden sabit bir sayıya eşittir."""
    # SHA-256("camera bags") özetinin ilk 8 byte'ı
    assert MockTrendsClient._seed("camera bags") == 17081425337466377474
    assert MockTrendsClient._seed("camera bags") != MockTrendsClient._seed("backpacks")


def test_formatted_values(settings):
    """formatted_value Google'ın biçimini taklit eder."""
    trends = fetch(MockTrendsClient(settings, stable=True), "Camera Bags")

    assert all(item.formatted_value == str(item.value) for item in trends.top)
    for item in trends.rising:
        if is_breakout(item.value, settings.BREAKOUT_THRESHOLD):
            assert item.formatted_value == "Breakout"
        else:
            assert item.formatted_value == f"+{item.value:,}%"


# --- Hata senaryoları ---

@pytest.mark.parametrize("keyword", ["fail", "Camera Fail Bags", "FAILURE test"])
def test_fail_keyword_raises_trends_error(settings, keyword):
    """İçinde "fail" geçen keyword genel tarama hatası fırlatır."""
    with pytest.raises(TrendsError) as error:
        fetch(MockTrendsClient(settings), keyword)

    assert type(error.value) is TrendsError


@pytest.mark.parametrize("keyword", ["empty", "Empty Box", "my EMPTY keyword"])
def test_empty_keyword_raises_empty_trends_error(settings, keyword):
    """İçinde "empty" geçen keyword boş veri hatası fırlatır."""
    with pytest.raises(EmptyTrendsError, match="Empty data returned"):
        fetch(MockTrendsClient(settings), keyword)


# --- Tekrar taramalarda varyasyon (demo modu) ---

@pytest.mark.parametrize("keyword", SAMPLE_KEYWORDS)
def test_first_scan_matches_stable_result(settings, keyword):
    """Varsayılan (stable=False) client'ın ilk taraması temel sonuçla aynıdır."""
    assert fetch(MockTrendsClient(settings), keyword) == fetch(MockTrendsClient(settings, stable=True), keyword)


@pytest.mark.parametrize("keyword", SAMPLE_KEYWORDS)
def test_repeated_scans_vary_but_stay_valid(settings, keyword):
    """Aynı keyword tekrar tarandığında sorgular girer/çıkar, değerler ±%20 oynar, biçim korunur."""
    client = MockTrendsClient(settings)
    first = fetch(client, keyword)

    for _ in range(5):
        later = fetch(client, keyword)
        assert_valid_shape(later, keyword, settings.BREAKOUT_THRESHOLD)

        for before, after in ((first.top, later.top), (first.rising, later.rising)):
            before_values = {item.query: item.value for item in before}
            after_values = {item.query: item.value for item in after}

            # Listeye en az bir sorgu girmiş veya listeden çıkmış olmalı
            assert before_values.keys() != after_values.keys()
            assert len(before_values.keys() ^ after_values.keys()) <= 4

            # Ortak sorguların değeri en fazla %20 oynar (breakout tabanına çekilen ilk sonuç hariç)
            for query in before_values.keys() & after_values.keys():
                old, new = before_values[query], after_values[query]
                if new == settings.BREAKOUT_THRESHOLD:
                    continue
                assert abs(new - old) <= old * VARIATION_RATIO + ROUNDING_TOLERANCE


def test_repeated_scans_are_reproducible(settings):
    """Varyasyon da deterministiktir: iki ayrı client aynı tarama sırasında aynı sonucu verir."""
    first_client, second_client = MockTrendsClient(settings), MockTrendsClient(settings)

    first_run = [fetch(first_client, "Camera Bags") for _ in range(3)]
    second_run = [fetch(second_client, "Camera Bags") for _ in range(3)]

    assert first_run == second_run
    assert first_run[0] != first_run[1] != first_run[2]


def test_scan_counter_is_per_keyword(settings):
    """Bir keyword'ün tekrar taranması diğer keyword'ün sonucunu etkilemez."""
    client = MockTrendsClient(settings)
    fetch(client, "Camera Bags")
    fetch(client, "Camera Bags")

    assert fetch(client, "Backpacks") == fetch(MockTrendsClient(settings, stable=True), "Backpacks")


# --- Bekleme davranışı ---

def test_delay_is_applied_per_keyword(monkeypatch):
    """Her keyword için MOCK_DELAY_SECONDS kadar beklenir."""
    sleeps: list[float] = []
    monkeypatch.setattr(mock_client.time, "sleep", sleeps.append)
    client = MockTrendsClient(Settings(_env_file=None, MOCK_DELAY_SECONDS=0.5))

    fetch(client, "Camera Bags")
    fetch(client, "Backpacks")

    assert sleeps == [0.5, 0.5]


def test_no_sleep_when_delay_is_zero(settings, monkeypatch):
    """Test ayarlarında (gecikme 0) hiç beklenmez; pause_between_keywords da beklemez."""
    sleeps: list[float] = []
    monkeypatch.setattr(mock_client.time, "sleep", sleeps.append)
    client = MockTrendsClient(settings)

    fetch(client, "Camera Bags")
    client.pause_between_keywords()

    assert sleeps == []
