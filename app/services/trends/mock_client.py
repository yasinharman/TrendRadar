# Ağ isteği atmayan, deterministik sahte veri üreten client
# Cookie olmadan uçtan uca çalışabilmek, demo ve testler için kullanılır

import hashlib
import random
import time

from app.core.config import Settings
from app.services.trends.base import (
    EmptyTrendsError,
    KeywordTrends,
    TrendItem,
    TrendsError,
    is_breakout,
    normalize_keyword,
)

# Gerçekçi görünen sorgu şablonları
TOP_TEMPLATES = [
    "best {keyword}",
    "{keyword} for women",
    "{keyword} for men",
    "leather {keyword}",
    "cheap {keyword}",
    "small {keyword}",
    "{keyword} near me",
    "{keyword} sale",
    "{keyword} amazon",
    "personalized {keyword}",
    "waterproof {keyword}",
    "{keyword} for travel",
    "luxury {keyword}",
    "{keyword} with zipper",
]

RISING_TEMPLATES = [
    "{keyword} 2026",
    "{keyword} black friday",
    "{keyword} tiktok",
    "viral {keyword}",
    "{keyword} dupe",
    "sustainable {keyword}",
    "{keyword} gift ideas",
    "custom {keyword}",
    "{keyword} trend",
    "minimalist {keyword}",
    "{keyword} aesthetic",
    "{keyword} unboxing",
]
# Şablonlar tanımlandı

# Hata senaryolarını tetikleyen işaret kelimeleri (partial/failed durumlarını denemek için)
FAIL_MARKER = "fail"
EMPTY_MARKER = "empty"

# Liste uzunlukları
TOP_COUNT_MIN = 5
TOP_COUNT_MAX = 10
RISING_COUNT_MIN = 3
RISING_COUNT_MAX = 8

# Top değerleri: ilk sonuç her zaman 100, diğerleri bu aralıkta
TOP_VALUE_MAX = 100
TOP_VALUE_MIN = 5
TOP_VALUE_FLOOR = 1   # Tekrar taramadaki oynamadan sonra bile değer bunun altına inmez

# Rising değerleri: breakout olmayanlar eşiğin bu oranları arasında, Google gibi yuvarlak sayılar
RISING_MIN_RATIO = 0.01
RISING_MAX_RATIO = 0.95
RISING_VALUE_STEP = 10

# Breakout değerleri: eşik ile eşiğin bu katı arasında (ör. 5000–200000)
BREAKOUT_MAX_MULTIPLIER = 40
BREAKOUT_VALUE_STEP = 50
EXTRA_BREAKOUT_CHANCE = 0.15

# Tekrar taramalardaki oynama: değerler ±%20, listeye 1–2 sorgu girer / çıkar
VARIATION_RATIO = 0.2
SWAP_COUNT_MIN = 1
SWAP_COUNT_MAX = 2

# Seed, SHA-256 özetinin ilk bu kadar byte'ından üretilir
SEED_BYTES = 8
# Üretim sabitleri tanımlandı


class MockTrendsClient:
    """Keyword'den türetilen sabit bir seed ile sahte ama tutarlı sonuçlar üretir."""

    def __init__(self, settings: Settings, stable: bool = False):
        """stable=True ise aynı keyword hep aynı sonucu döner; False ise tekrar taramalarda sonuç oynar."""
        self.settings = settings
        self.stable = stable
        # Keyword başına kaç kez tarandığını tutan sayaç (yalnızca stable=False iken kullanılır)
        self._call_counts: dict[str, int] = {}

    # --- Client arayüzü ---

    def fetch_keyword(self, keyword: str, geo: str, timeframe: str) -> KeywordTrends:
        """Keyword için deterministik top ve rising listeleri üretir."""
        normalized = normalize_keyword(keyword)

        # Gerçek bir isteğin süresini taklit et
        if self.settings.MOCK_DELAY_SECONDS > 0:
            time.sleep(self.settings.MOCK_DELAY_SECONDS)
        # Bekleme tamamlandı

        # İşaret kelimesi içeren keyword'ler için hata senaryoları
        if FAIL_MARKER in normalized:
            raise TrendsError("Mock: bu keyword için tarama hatası simüle edildi")
        if EMPTY_MARKER in normalized:
            raise EmptyTrendsError("Boş veri döndü (top ve rising her ikisi de boş)")
        # Hata senaryoları kontrol edildi

        # Temel sonuç yalnızca keyword'e bağlıdır: aynı keyword → aynı liste
        seed = self._seed(normalized)
        rng = random.Random(seed)
        top = self._generate_top(normalized, rng)
        rising = self._generate_rising(normalized, rng)
        # Temel sonuç üretildi

        # Demo modunda ikinci ve sonraki taramalarda sonucu oynat (diff anlamlı olsun)
        call_index = self._next_call_index(normalized)
        if call_index > 0:
            variation_rng = random.Random(seed + call_index)
            top = self._vary_top(top, normalized, variation_rng)
            rising = self._vary_rising(rising, normalized, variation_rng)
        # Varyasyon uygulandı

        return KeywordTrends(
            top=[TrendItem(query, value, str(value)) for query, value in top],
            rising=[TrendItem(query, value, self._format_rising(value)) for query, value in rising],
        )

    def pause_between_keywords(self) -> None:
        """Mock modda keyword'ler arasında beklemeye gerek yoktur."""

    # --- Seed ve sayaç ---

    @staticmethod
    def _seed(normalized: str) -> int:
        """Keyword'den süreçler arasında değişmeyen bir seed üretir (Python'un hash()'i her süreçte değişir)."""
        digest = hashlib.sha256(normalized.encode("utf-8")).digest()
        return int.from_bytes(digest[:SEED_BYTES], "big")

    def _next_call_index(self, normalized: str) -> int:
        """Bu keyword'ün kaçıncı kez tarandığını döner (ilk tarama 0); stable modda hep 0."""
        if self.stable:
            return 0
        call_index = self._call_counts.get(normalized, 0)
        self._call_counts[normalized] = call_index + 1
        return call_index

    # --- Temel sonuç üretimi ---

    def _generate_top(self, keyword: str, rng: random.Random) -> list[tuple[str, int]]:
        """5–10 sorgu üretir; ilki 100, diğerleri azalan değerlerdedir."""
        count = rng.randint(TOP_COUNT_MIN, TOP_COUNT_MAX)
        queries = [template.format(keyword=keyword) for template in rng.sample(TOP_TEMPLATES, count)]
        lower_values = sorted(rng.sample(range(TOP_VALUE_MIN, TOP_VALUE_MAX), count - 1), reverse=True)
        return list(zip(queries, [TOP_VALUE_MAX] + lower_values))

    def _generate_rising(self, keyword: str, rng: random.Random) -> list[tuple[str, int]]:
        """3–8 sorgu üretir; en az biri breakout'tur, değerler büyükten küçüğe sıralıdır."""
        count = rng.randint(RISING_COUNT_MIN, RISING_COUNT_MAX)
        queries = [template.format(keyword=keyword) for template in rng.sample(RISING_TEMPLATES, count)]

        # İlk değer her zaman breakout; diğerleri küçük bir olasılıkla breakout olur
        values = [self._breakout_value(rng)]
        for _ in range(count - 1):
            if rng.random() < EXTRA_BREAKOUT_CHANCE:
                values.append(self._breakout_value(rng))
            else:
                values.append(self._regular_rising_value(rng))
        values.sort(reverse=True)
        # Değerler üretildi

        return list(zip(queries, values))

    def _breakout_value(self, rng: random.Random) -> int:
        """Breakout eşiğinin üzerinde yuvarlak bir değer üretir."""
        threshold = self.settings.BREAKOUT_THRESHOLD
        return rng.randrange(threshold, threshold * BREAKOUT_MAX_MULTIPLIER + 1, BREAKOUT_VALUE_STEP)

    def _regular_rising_value(self, rng: random.Random) -> int:
        """Breakout eşiğinin altında yuvarlak bir yüzde artış değeri üretir."""
        value = int(self.settings.BREAKOUT_THRESHOLD * rng.uniform(RISING_MIN_RATIO, RISING_MAX_RATIO))
        return value - value % RISING_VALUE_STEP

    def _format_rising(self, value: int) -> str:
        """Google'ın formattedValue alanını taklit eder: "Breakout" veya "+800%"."""
        if is_breakout(value, self.settings.BREAKOUT_THRESHOLD):
            return "Breakout"
        return f"+{value:,}%"

    # --- Tekrar taramalar için varyasyon ---

    def _vary_top(self, items: list[tuple[str, int]], keyword: str, rng: random.Random) -> list[tuple[str, int]]:
        """Top listesinde değerleri oynatır ve birkaç sorguyu değiştirir; ilk sonuç 100 kalır."""
        # İlk sonuç dışındaki değerleri ±%20 oynat (100'ü geçemez)
        leader, others = items[0], items[1:]
        varied = [
            (query, min(TOP_VALUE_MAX - 1, max(TOP_VALUE_FLOOR, round(value * self._variation_factor(rng)))))
            for query, value in others
        ]
        # Değerler oynatıldı

        # Sorgu giriş/çıkışını uygula ve yeniden sırala
        swapped = self._swap_queries(
            [leader] + varied, TOP_TEMPLATES, keyword, rng, TOP_COUNT_MIN, TOP_COUNT_MAX,
            new_value=lambda: rng.randint(TOP_VALUE_MIN, TOP_VALUE_MAX - 1),
        )
        return sorted(swapped, key=lambda item: item[1], reverse=True)

    def _vary_rising(self, items: list[tuple[str, int]], keyword: str, rng: random.Random) -> list[tuple[str, int]]:
        """Rising listesinde değerleri oynatır ve birkaç sorguyu değiştirir; en az bir breakout kalır."""
        # Tüm değerleri ±%20 oynat ve yuvarla
        varied = []
        for query, value in items:
            new_value = int(value * self._variation_factor(rng))
            varied.append((query, new_value - new_value % RISING_VALUE_STEP))
        # Değerler oynatıldı

        # İlk sonuç eşiğin altına düşmesin: listede her zaman bir breakout bulunur
        leader_query, leader_value = varied[0]
        varied[0] = (leader_query, max(leader_value, self.settings.BREAKOUT_THRESHOLD))
        # Breakout garantisi sağlandı

        # Sorgu giriş/çıkışını uygula ve yeniden sırala
        swapped = self._swap_queries(
            varied, RISING_TEMPLATES, keyword, rng, RISING_COUNT_MIN, RISING_COUNT_MAX,
            new_value=lambda: self._regular_rising_value(rng),
        )
        return sorted(swapped, key=lambda item: item[1], reverse=True)

    @staticmethod
    def _variation_factor(rng: random.Random) -> float:
        """0.8 ile 1.2 arasında rastgele bir çarpan üretir."""
        return rng.uniform(1 - VARIATION_RATIO, 1 + VARIATION_RATIO)

    @staticmethod
    def _swap_queries(items, templates, keyword, rng, min_count, max_count, new_value) -> list[tuple[str, int]]:
        """Listeden 1–2 sorgu çıkarır ve kullanılmamış şablonlardan 1–2 yeni sorgu ekler.

        İlk sonuç hiç çıkarılmaz ve liste uzunluğu min_count–max_count aralığında kalır.
        """
        # Henüz listede olmayan sorgular (çıkarılan sorgu geri eklenmesin diye önce hesaplanır)
        existing = {query for query, _ in items}
        unused = [query for query in (t.format(keyword=keyword) for t in templates) if query not in existing]
        # Aday sorgular belirlendi

        # İlk sonuç dışından sorgu çıkar
        drop_count = min(rng.randint(SWAP_COUNT_MIN, SWAP_COUNT_MAX), len(items) - min_count)
        dropped = set(rng.sample(items[1:], drop_count))
        kept = [item for item in items if item not in dropped]
        # Çıkarma tamamlandı

        # Yer varsa yeni sorgu ekle
        add_count = min(rng.randint(SWAP_COUNT_MIN, SWAP_COUNT_MAX), max_count - len(kept), len(unused))
        added = [(query, new_value()) for query in rng.sample(unused, add_count)]
        # Ekleme tamamlandı

        return kept + added
