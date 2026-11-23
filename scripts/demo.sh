#!/usr/bin/env bash
# TrendRadar API demo akışı
# İş oluştur → durumu izle → sonuçları al → CSV indir → aynı ürünü tekrar tara → diff
#
# Kullanım:
#   scripts/demo.sh                          # http://127.0.0.1:8000 adresindeki API'ye karşı
#   BASE_URL=http://127.0.0.1:9000 scripts/demo.sh
#   scripts/demo.sh "Wine Carriers" "Aprons" # kendi ürün adlarınızla
#
# Gereksinim: curl ve python3. API mock modda çalışıyor olmalı (varsayılan).
# Not: diff'in fark göstermesi için iki tarama aynı sunucu oturumunda yapılmalıdır; mock client
# tekrar taramalardaki oynamayı bellekte tuttuğu bir sayaçla üretir.

set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
POLL_INTERVAL_SECONDS=1
POLL_MAX_ATTEMPTS=120
PREVIEW_ROWS=5

# Taranacak ürünler: argüman verilmezse örnek liste kullanılır
if [ "$#" -gt 0 ]; then
    KEYWORDS=("$@")
else
    KEYWORDS=("Camera Bags" "Backpacks" "Tote Bags")
fi

# --- Yardımcılar ---

step() {
    # Adım başlığı yazdırır
    printf '\n\033[1m== %s\033[0m\n' "$1"
}

json_field() {
    # stdin'deki JSON nesnesinden tek bir alanı okur: json_field id
    python3 -c "import json, sys; print(json.load(sys.stdin)['$1'])"
}

keywords_json() {
    # Argümanları {"keywords": [...]} gövdesine çevirir
    python3 -c 'import json, sys; print(json.dumps({"keywords": sys.argv[1:]}))' "$@"
}

create_job() {
    # Verilen ürünler için tarama başlatır ve job id'sini yazdırır
    curl -sS --fail-with-body -X POST "$BASE_URL/jobs" \
        -H 'Content-Type: application/json' \
        -d "$(keywords_json "$@")" | json_field id
}

wait_for_job() {
    # Job bitene kadar ilerlemeyi yazdırır
    local job_id="$1" attempt status progress
    for ((attempt = 0; attempt < POLL_MAX_ATTEMPTS; attempt++)); do
        read -r status progress < <(
            curl -sS --fail-with-body "$BASE_URL/jobs/$job_id" |
                python3 -c "import json, sys; job = json.load(sys.stdin); print(job['status'], job['progress'])"
        )
        printf '   job %s: %-9s %s\n' "$job_id" "$status" "$progress"
        case "$status" in
            pending | running) sleep "$POLL_INTERVAL_SECONDS" ;;
            *) return 0 ;;
        esac
    done
    echo "Job $job_id zamanında bitmedi" >&2
    return 1
}

# --- Akış ---

step "0) API erişilebilir mi?"
if ! HEALTH="$(curl -sS --fail-with-body "$BASE_URL/health" 2>/dev/null)"; then
    echo "   API'ye ulaşılamadı: $BASE_URL" >&2
    echo "   Önce sunucuyu başlatın: uvicorn app.main:app  (veya: docker compose up --build)" >&2
    exit 1
fi
echo "$HEALTH" | python3 -m json.tool

step "1) Tarama başlat: ${KEYWORDS[*]}"
FIRST_JOB="$(create_job "${KEYWORDS[@]}")"
echo "   Oluşturulan job: $FIRST_JOB (yanıt hemen döndü; tarama arka planda)"

step "2) Durumu izle"
wait_for_job "$FIRST_JOB"
curl -sS --fail-with-body "$BASE_URL/jobs/$FIRST_JOB" | python3 -c "
import json, sys
for keyword in json.load(sys.stdin)['keywords']:
    print(f\"   {keyword['keyword']:<20} {keyword['status']:<8} top={keyword['top_count']} rising={keyword['rising_count']}\")
"

step "3) Sonuçlar: yükselişteki breakout aramalar"
curl -sS --fail-with-body "$BASE_URL/jobs/$FIRST_JOB/results?type=rising&breakout_only=true" | python3 -c "
import json, sys
page = json.load(sys.stdin)
print(f\"   {page['total']} breakout bulundu\")
for item in page['items'][:$PREVIEW_ROWS]:
    print(f\"   {item['seed_keyword']:<20} {item['query']:<40} +{item['value']}%\")
"

step "4) CSV indir"
CSV_FILE="$(mktemp -d)/trendradar_job_${FIRST_JOB}.csv"
curl -sS --fail-with-body -o "$CSV_FILE" "$BASE_URL/jobs/$FIRST_JOB/export.csv"
echo "   Kaydedildi: $CSV_FILE ($(($(wc -l < "$CSV_FILE") - 1)) satır)"
head -n "$PREVIEW_ROWS" "$CSV_FILE" | sed 's/^/   /'

step "5) Aynı ürünleri tekrar tara"
SECOND_JOB="$(create_job "${KEYWORDS[@]}")"
wait_for_job "$SECOND_JOB"

step "6) İki tarama arasındaki fark (diff $FIRST_JOB → $SECOND_JOB)"
curl -sS --fail-with-body "$BASE_URL/insights/diff?from_job=$FIRST_JOB&to_job=$SECOND_JOB" | python3 -c "
import json, sys
diff = json.load(sys.stdin)
for warning in diff['warnings']:
    print(f'   uyarı: {warning}')
for keyword in diff['keywords']:
    print(f\"   {keyword['keyword']}\")
    for query_type in ('top', 'rising'):
        section = keyword[query_type]
        print(f\"     {query_type:<7} yeni: {len(section['new'])}  çıkan: {len(section['dropped'])}  değişen: {len(section['changed'])}\")
        for item in section['new']:
            print(f\"       + {item['query']} ({item['value']})\")
        for item in section['dropped']:
            print(f\"       - {item['query']} ({item['value']})\")
"

step "7) İlk ürünün tarama geçmişi"
FIRST_KEYWORD_ENCODED="$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1]))' "${KEYWORDS[0]}")"
curl -sS --fail-with-body "$BASE_URL/keywords/$FIRST_KEYWORD_ENCODED/history" | python3 -c "
import json, sys
history = json.load(sys.stdin)
print(f\"   {history['keyword']}: {len(history['scans'])} tarama\")
for scan in history['scans'][:$PREVIEW_ROWS]:
    print(f\"   job {scan['job_id']}  {scan['scanned_at']}  top={len(scan['top'])} rising={len(scan['rising'])}\")
"

step "Bitti"
echo "   Swagger arayüzü: $BASE_URL/docs"
