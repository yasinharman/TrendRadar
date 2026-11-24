// TrendRadar web arayüzü
// Yalnızca mevcut REST API'yi çağırır; iş mantığının tamamı sunucudadır.
//
// Güvenlik: API'den gelen metinler (özellikle Google'dan gelen sorgular) güvenilmez veridir.
// Bu dosyada DOM'a metin yalnızca textContent ile yazılır; HTML olarak yorumlanan hiçbir
// yazma yöntemi kullanılmaz.
"use strict";

// --- Sabitler ---

// Kuyrukta iş varken durumun yenilenme aralığı
const POLL_INTERVAL_MS = 2000;

// Tabloların sayfa boyutları
const JOBS_PAGE_SIZE = 10;
const RESULTS_PAGE_SIZE = 25;
const BREAKOUTS_PAGE_SIZE = 25;

// Bilgi/hata mesajının ekranda kalma süresi
const NOTICE_TIMEOUT_MS = 6000;

// Gövdesi olmayan başarılı yanıt (DELETE)
const HTTP_NO_CONTENT = 204;

// İlerleme çubuğunun tam genişliği (yüzde)
const FULL_PERCENT = 100;

// İş durumları: süren işler izlenir, başarılı bitenlerin sonuçları indirilebilir/karşılaştırılabilir
const ACTIVE_STATUSES = ["pending", "running"];
const SUCCESSFUL_STATUSES = ["completed", "partial"];

// API değerlerinin ekranda görünen karşılıkları
const JOB_STATUS_LABELS = {
    pending: "Bekliyor",
    running: "Çalışıyor",
    completed: "Tamamlandı",
    partial: "Kısmi",
    failed: "Başarısız",
};
const KEYWORD_STATUS_LABELS = {
    pending: "Bekliyor",
    success: "Başarılı",
    failed: "Başarısız",
};
// Çalışan taramada o an işlenen ürünün rozeti
const SCANNING_LABEL = "Taranıyor";
const TYPE_LABELS = {
    top: "Top",
    rising: "Rising",
};
const TIMEFRAME_LABELS = {
    "now 7-d": "Son 7 gün",
    "today 1-m": "Son 1 ay",
    "today 3-m": "Son 3 ay",
    "today 12-m": "Son 12 ay",
    "today 5-y": "Son 5 yıl",
};
const MODE_LABELS = {
    mock: "Mock mod (örnek veri)",
    live: "Live mod (gerçek Google Trends)",
};

// Ülke kodu boşsa gösterilen metin
const WORLDWIDE_LABEL = "Dünya";

// Sayıların, tarihlerin ve ülke adlarının biçimi
const LOCALE = "tr-TR";

// Ülke listesi: ISO 3166-1 alpha-2 kodlarının tamamı. Adlar burada tutulmaz; tarayıcının
// yerleşik ülke adlarından (Intl.DisplayNames) Türkçe olarak üretilir
const COUNTRY_CODES = (
    "AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ " +
    "CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR " +
    "GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT " +
    "JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY " +
    "MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM " +
    "PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ " +
    "TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW"
).split(" ");

// Listenin başına alınan ülke (en sık seçilen)
const PINNED_COUNTRY_CODE = "TR";

// Çalışma göstergesindeki sürenin yenilenme aralığı
const ELAPSED_TICK_MS = 1000;

// Süre hesabı birimleri
const MS_PER_SECOND = 1000;
const SECONDS_PER_MINUTE = 60;

// Live modda bir ürün bu kadar saniyeden uzun sürerse gecikmenin nedeni kullanıcıya açıklanır
const SLOW_KEYWORD_SECONDS = 45;

// Çalışma göstergesinde duruma göre gösterilen açıklamalar
const ACTIVITY_HINTS = {
    pending: "Taramalar sırayla işlenir; sıra gelince kendiliğinden başlar.",
    mock: "Örnek veri üretiliyor.",
    live: "Google Trends taranıyor; her ürün yaklaşık 10–25 saniye sürer.",
    slow:
        "Bu ürün beklenenden uzun sürüyor. Google büyük olasılıkla hız sınırı uyguladı; uygulama bekleyip " +
        "kendiliğinden yeniden deniyor. Sayfayı açık tutmanıza gerek yok, tarama sunucuda sürer.",
};

// --- Durum ---

const state = {
    jobsOffset: 0,
    selectedJobId: null,
    selectedJobStatus: null,
    confirmDeleteId: null,
    resultsOffset: 0,
    breakoutsOffset: 0,
    pollTimer: null,
    noticeTimer: null,
    // Çalışma göstergesi: sunucunun modu, süren seçili tarama ve ilerlemenin en son değiştiği an
    mode: null,
    activeJob: null,
    progressKey: null,
    progressChangedAt: 0,
};

// Tarayıcının yerleşik Türkçe ülke adları (desteklenmiyorsa kodlar gösterilir)
const regionNames = typeof Intl.DisplayNames === "function" ? new Intl.DisplayNames([LOCALE], { type: "region" }) : null;

// --- Genel yardımcılar ---

/** id'si verilen öğeyi döner. */
function byId(id) {
    return document.getElementById(id);
}

/**
 * Yeni bir DOM öğesi oluşturur.
 * Metin her zaman textContent ile yazılır; böylece API'den gelen değerler HTML olarak yorumlanmaz.
 */
function el(tag, options = {}, children = []) {
    const node = document.createElement(tag);
    if (options.text !== undefined) {
        node.textContent = options.text;
    }
    if (options.className) {
        node.className = options.className;
    }
    for (const [name, value] of Object.entries(options.attrs || {})) {
        node.setAttribute(name, value);
    }
    if (options.onClick) {
        node.addEventListener("click", options.onClick);
    }
    node.append(...children);
    return node;
}

/** Hücre listesinden bir tablo satırı üretir; hücre metin veya hazır bir DOM öğesi olabilir. */
function tableRow(cells, numericColumns = []) {
    const row = el("tr");
    cells.forEach((cell, index) => {
        const td = el("td", { className: numericColumns.includes(index) ? "num" : "" });
        if (cell instanceof Node) {
            td.append(cell);
        } else {
            td.textContent = cell === null || cell === undefined ? "" : String(cell);
        }
        row.append(td);
    });
    return row;
}

/** Durum rozeti üretir; sınıf adı renk seçimini belirler. */
function badge(text, kind) {
    return el("span", { text, className: `badge ${kind}` });
}

/** ISO zaman damgasını yerel saatle okunur biçime çevirir. */
function formatDate(value) {
    return value ? new Date(value).toLocaleString(LOCALE) : "—";
}

/** Sayıyı binlik ayraçlarıyla yazar. */
function formatNumber(value) {
    return Number(value).toLocaleString(LOCALE);
}

/** Sonuç değerini türüne göre yazar: rising yüzde artıştır, top 0–100 skorudur. */
function formatValue(type, value) {
    return type === "rising" ? `+${formatNumber(value)}%` : formatNumber(value);
}

/** Ülke kodunu yazar; boş kod tüm dünya demektir. */
function formatGeo(geo) {
    return geo ? `${countryName(geo)} (${geo})` : WORLDWIDE_LABEL;
}

/** Ülke kodunun Türkçe adını döner; ad bulunamazsa kodun kendisini. */
function countryName(code) {
    try {
        return (regionNames && regionNames.of(code)) || code;
    } catch {
        return code;
    }
}

/** Saniyeyi "45 sn" veya "2 dk 05 sn" biçiminde yazar. */
function formatDuration(totalSeconds) {
    const seconds = Math.max(Math.floor(totalSeconds), 0);
    const minutes = Math.floor(seconds / SECONDS_PER_MINUTE);
    const rest = seconds % SECONDS_PER_MINUTE;
    return minutes === 0 ? `${rest} sn` : `${minutes} dk ${String(rest).padStart(2, "0")} sn`;
}

/** Zaman aralığının okunur adını döner. */
function formatTimeframe(timeframe) {
    return TIMEFRAME_LABELS[timeframe] || timeframe;
}

/** Ekranın üstünde bilgi veya hata mesajı gösterir; bir süre sonra kendiliğinden kaybolur. */
function showNotice(message, kind = "info") {
    const notice = byId("notice");
    notice.textContent = message;
    notice.className = `notice ${kind}`;
    notice.hidden = false;
    clearTimeout(state.noticeTimer);
    state.noticeTimer = setTimeout(() => {
        notice.hidden = true;
    }, NOTICE_TIMEOUT_MS);
}

/** Ekrandaki mesajı kaldırır (yeni bir işlem başlarken eskisi kalmasın). */
function hideNotice() {
    clearTimeout(state.noticeTimer);
    byId("notice").hidden = true;
}

/** Bir işlemi çalıştırır; hata olursa mesajını ekranda gösterir. */
async function guarded(action) {
    try {
        await action();
    } catch (error) {
        showNotice(error.message, "error");
    }
}

// --- API ---

/** API'nin hata gövdesinden okunur bir mesaj çıkarır (düz metin veya 422 alan listesi). */
function errorMessage(data, status) {
    const detail = data && data.detail;
    if (typeof detail === "string") {
        return detail;
    }
    if (Array.isArray(detail)) {
        // 422: her kayıt hatalı alanın yolunu ve mesajını taşır; baştaki "body"/"query" atılır
        return detail
            .map((item) => {
                const field = (item.loc || []).slice(1).join(".");
                return field ? `${field}: ${item.msg}` : item.msg;
            })
            .join(" • ");
    }
    return `İstek başarısız oldu (HTTP ${status})`;
}

/**
 * API'ye istek atar ve JSON yanıtını döner.
 * Boş, null ve false parametreler gönderilmez. Hata durumunda mesajı okunur bir Error fırlatır.
 */
async function api(path, options = {}) {
    // Adresi ve query parametrelerini hazırla
    const url = new URL(path, window.location.origin);
    for (const [name, value] of Object.entries(options.params || {})) {
        if (value !== "" && value !== null && value !== undefined && value !== false) {
            url.searchParams.set(name, value);
        }
    }
    // Adres hazır

    // İsteği hazırla
    const init = { method: options.method || "GET", headers: {} };
    if (options.body !== undefined) {
        init.headers["Content-Type"] = "application/json";
        init.body = JSON.stringify(options.body);
    }
    // İstek hazır

    let response;
    try {
        response = await fetch(url, init);
    } catch {
        throw new Error("Sunucuya ulaşılamadı");
    }
    if (response.status === HTTP_NO_CONTENT) {
        return null;
    }

    // Gövde JSON değilse (ör. vekil sunucunun hata sayfası) durum koduyla yetinilir
    let data = null;
    try {
        data = await response.json();
    } catch {
        data = null;
    }
    if (!response.ok) {
        throw new Error(errorMessage(data, response.status));
    }
    return data;
}

// --- Sayfalama ---

/** "Önceki / 1–25 / 120 / Sonraki" denetimini çizer; sayfa değişince onChange(yeniOffset) çağrılır. */
function renderPager(container, page, onChange) {
    container.replaceChildren();
    if (page.total <= page.limit && page.offset === 0) {
        return;
    }
    const first = page.total === 0 ? 0 : page.offset + 1;
    const last = Math.min(page.offset + page.limit, page.total);

    const previous = el("button", {
        text: "Önceki",
        className: "small",
        attrs: { type: "button" },
        onClick: () => onChange(Math.max(page.offset - page.limit, 0)),
    });
    previous.disabled = page.offset === 0;

    const next = el("button", {
        text: "Sonraki",
        className: "small",
        attrs: { type: "button" },
        onClick: () => onChange(page.offset + page.limit),
    });
    next.disabled = last >= page.total;

    container.append(previous, el("span", { text: `${first}–${last} / ${page.total}`, className: "muted" }), next);
}

// --- Üst şerit ---

/** Çalışma modunu ve kuyruk durumunu günceller; sağlık yanıtını döner. */
async function refreshHealth() {
    const health = await api("/health");
    state.mode = health.mode;

    const modeBadge = byId("mode-badge");
    modeBadge.textContent = MODE_LABELS[health.mode] || health.mode;
    modeBadge.className = `badge ${health.mode}`;
    modeBadge.hidden = false;

    const { running, pending } = health.queue;
    byId("queue-info").textContent =
        running + pending === 0 ? "Kuyruk boş" : `Kuyruk: ${running} çalışıyor, ${pending} bekliyor`;
    return health;
}

// --- Tarama listesi ---

/** Bir işin ilerleme metnini üretir; bekleyen işte kuyruk sırası da yazılır. */
function progressText(job) {
    if (job.status === "pending" && job.queue_position !== null) {
        return job.queue_position === 0 ? `${job.progress} · sıradaki` : `${job.progress} · önünde ${job.queue_position} iş`;
    }
    return job.progress;
}

/** Silme düğmesini (veya onay bekleniyorsa "Evet / Vazgeç" çiftini) üretir. */
function deleteControls(job) {
    const cell = el("td", { className: "actions" });

    // Onay adımı: tarayıcı penceresi açmak yerine satır içinde sorulur
    if (state.confirmDeleteId === job.id) {
        cell.append(
            el("span", { text: "Silinsin mi?", className: "muted" }),
            el("button", {
                text: "Evet",
                className: "small danger",
                attrs: { type: "button" },
                onClick: (event) => {
                    event.stopPropagation();
                    guarded(() => deleteJob(job.id));
                },
            }),
            el("button", {
                text: "Vazgeç",
                className: "small",
                attrs: { type: "button" },
                onClick: (event) => {
                    event.stopPropagation();
                    state.confirmDeleteId = null;
                    guarded(refreshJobs);
                },
            }),
        );
        return cell;
    }
    // Onay beklenmiyor

    const button = el("button", {
        text: "Sil",
        className: "small danger",
        attrs: { type: "button" },
        onClick: (event) => {
            event.stopPropagation();
            state.confirmDeleteId = job.id;
            guarded(refreshJobs);
        },
    });
    // Çalışan iş silinemez (API 409 döner)
    if (job.status === "running") {
        button.disabled = true;
        button.title = "Çalışan tarama silinemez";
    }
    cell.append(button);
    return cell;
}

/** Tarama listesini sunucudan alır ve tabloyu yeniden çizer. */
async function refreshJobs() {
    const page = await api("/jobs", { params: { limit: JOBS_PAGE_SIZE, offset: state.jobsOffset } });

    // Son sayfadaki tek kayıt silindiyse bir önceki sayfaya dön
    if (page.items.length === 0 && page.offset > 0) {
        state.jobsOffset = Math.max(page.offset - JOBS_PAGE_SIZE, 0);
        return refreshJobs();
    }
    // Sayfa geçerli

    const body = byId("jobs-body");
    body.replaceChildren();
    for (const job of page.items) {
        const row = tableRow([
            job.id,
            badge(JOB_STATUS_LABELS[job.status] || job.status, job.status),
            progressText(job),
            job.geo || WORLDWIDE_LABEL,
            formatTimeframe(job.timeframe),
            formatDate(job.created_at),
        ]);
        row.append(deleteControls(job));
        row.classList.add("clickable");
        row.classList.toggle("selected", job.id === state.selectedJobId);
        row.addEventListener("click", () => guarded(() => selectJob(job.id)));
        body.append(row);
    }

    byId("jobs-empty").hidden = page.total > 0;
    renderPager(byId("jobs-pager"), page, (offset) => {
        state.jobsOffset = offset;
        guarded(refreshJobs);
    });
    return page;
}

/** Formdaki ürünlerle yeni bir tarama başlatır. */
async function createJob() {
    // Satırları ürün listesine çevir; boş satırlar atılır
    const keywords = byId("job-keywords")
        .value.split("\n")
        .map((line) => line.trim())
        .filter((line) => line !== "");
    if (keywords.length === 0) {
        throw new Error("En az bir ürün adı yazın");
    }
    // Ürün listesi hazır

    // Ülke her zaman gönderilir (boş değer = tüm dünya); zaman aralığı seçilmediyse sunucu
    // kendi varsayılanını kullanır
    const body = { keywords, geo: byId("job-geo").value };
    const timeframe = byId("job-timeframe").value;
    if (timeframe !== "") {
        body.timeframe = timeframe;
    }
    // İstek gövdesi hazır

    const job = await api("/jobs", { method: "POST", body });
    byId("job-keywords").value = "";
    state.jobsOffset = 0;
    showNotice(`Tarama #${job.id} kuyruğa alındı`, "success");
    await selectJob(job.id);
    await tick();
}

/** Bir taramayı siler; seçili olan silindiyse detay paneli kapanır. */
async function deleteJob(jobId) {
    state.confirmDeleteId = null;
    await api(`/jobs/${jobId}`, { method: "DELETE" });
    if (state.selectedJobId === jobId) {
        closeJobDetail();
    }
    showNotice(`Tarama #${jobId} silindi`, "success");
    await tick();
}

// --- Tarama detayı ---

/** Detay panelini kapatır. */
function closeJobDetail() {
    state.selectedJobId = null;
    state.selectedJobStatus = null;
    state.activeJob = null;
    state.progressKey = null;
    byId("job-detail").hidden = true;
}

// --- Çalışma göstergesi ---

/** Süren taramada o an ne yapıldığını anlatan başlığı üretir. */
function activityTitle(job) {
    if (job.status === "pending") {
        return job.queue_position ? `Sırada bekliyor: önünde ${job.queue_position} tarama var` : "Sırada: birazdan başlayacak";
    }
    // Ürünler sırayla işlenir; henüz bekleyen ilk ürün şu an taranandır
    const current = job.keywords.find((item) => item.status === "pending");
    if (!current) {
        return "Tarama tamamlanıyor…";
    }
    return `Taranıyor: ${current.keyword} (${job.processed_keywords + 1}/${job.total_keywords})`;
}

/** Duruma göre açıklamayı seçer: sırada, örnek veri, gerçek tarama veya hız sınırı beklemesi. */
function activityHint(job) {
    if (job.status === "pending") {
        return ACTIVITY_HINTS.pending;
    }
    if (state.mode !== "live") {
        return ACTIVITY_HINTS.mock;
    }
    const keywordSeconds = (Date.now() - state.progressChangedAt) / MS_PER_SECOND;
    return keywordSeconds > SLOW_KEYWORD_SECONDS ? ACTIVITY_HINTS.slow : ACTIVITY_HINTS.live;
}

/**
 * Geçen süreyi ve açıklamayı yeniler. Sunucudan yeni veri gelmese de her saniye çalışır;
 * böylece uzun süren bir üründe sayfanın donmadığı görülür.
 */
function renderActivityTiming() {
    const job = state.activeJob;
    if (job === null) {
        return;
    }
    const since = new Date(job.started_at || job.created_at).getTime();
    byId("activity-elapsed").textContent = formatDuration((Date.now() - since) / MS_PER_SECOND);
    byId("activity-hint").textContent = activityHint(job);
}

/** Seçili tarama sürüyorsa çalışma göstergesini gösterir ve günceller, bittiyse gizler. */
function updateActivity(job) {
    const active = ACTIVE_STATUSES.includes(job.status);
    byId("detail-activity").hidden = !active;
    byId("detail-progress").classList.toggle("active", job.status === "running");
    state.activeJob = active ? job : null;
    if (!active) {
        state.progressKey = null;
        return;
    }

    // İlerleme değiştiğinde (ör. sıradaki ürüne geçildiğinde) ürün süresi yeniden başlar
    const progressKey = `${job.id}:${job.status}:${job.processed_keywords}`;
    if (state.progressKey !== progressKey) {
        state.progressKey = progressKey;
        state.progressChangedAt = Date.now();
    }
    // Ürün süresi güncel

    byId("activity-title").textContent = activityTitle(job);
    renderActivityTiming();
}

/** Sonuç filtrelerini başlangıç değerlerine döndürür. */
function resetResultFilters() {
    byId("results-form").reset();
    state.resultsOffset = 0;
}

/** Bir taramayı seçer: detayını ve sonuçlarını gösterir. */
async function selectJob(jobId) {
    state.selectedJobId = jobId;
    state.selectedJobStatus = null;
    state.activeJob = null;
    resetResultFilters();
    byId("results-keyword").replaceChildren(el("option", { text: "Hepsi", attrs: { value: "" } }));
    showView("jobs");
    await refreshSelectedJob();
    await refreshJobs();
    byId("job-detail").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

/** Ürün filtresinin seçeneklerini işin ürünleriyle doldurur (yalnızca ilk yüklemede). */
function fillKeywordFilter(keywords) {
    const select = byId("results-keyword");
    if (select.options.length > 1) {
        return;
    }
    for (const item of keywords) {
        select.append(el("option", { text: item.keyword, attrs: { value: item.keyword } }));
    }
}

/** Seçili taramanın başlığını, ürün özetini ve sonuçlarını sunucudan alıp çizer. */
async function refreshSelectedJob() {
    const jobId = state.selectedJobId;
    if (jobId === null) {
        return;
    }
    const job = await api(`/jobs/${jobId}`);

    // Yanıt beklenirken başka bir tarama seçildiyse bu yanıt çizilmez
    if (state.selectedJobId !== jobId) {
        return;
    }
    state.selectedJobStatus = job.status;
    // Yanıt hâlâ geçerli

    // Başlık, durum ve zamanlar
    byId("detail-title").textContent = `Tarama #${job.id}`;
    const statusBadge = byId("detail-status");
    statusBadge.textContent = JOB_STATUS_LABELS[job.status] || job.status;
    statusBadge.className = `badge ${job.status}`;
    byId("detail-meta").textContent = [
        `Ülke: ${formatGeo(job.geo)}`,
        `Aralık: ${formatTimeframe(job.timeframe)}`,
        `İlerleme: ${progressText(job)}`,
        `Oluşturulma: ${formatDate(job.created_at)}`,
        `Başlangıç: ${formatDate(job.started_at)}`,
        `Bitiş: ${formatDate(job.finished_at)}`,
    ].join("  ·  ");
    const percent = job.total_keywords === 0 ? 0 : (job.processed_keywords / job.total_keywords) * FULL_PERCENT;
    byId("detail-progress-bar").style.width = `${percent}%`;
    updateActivity(job);
    // Başlık çizildi

    // İş seviyesindeki hata (normalde boş)
    const errorText = byId("detail-error");
    errorText.textContent = job.error_message || "";
    errorText.hidden = !job.error_message;

    // CSV yalnızca sonuç üretmiş işler için indirilebilir
    const csvLink = byId("detail-csv");
    csvLink.hidden = !SUCCESSFUL_STATUSES.includes(job.status);
    csvLink.href = `/jobs/${job.id}/export.csv`;
    csvLink.setAttribute("download", `trendradar_job_${job.id}.csv`);

    // Ürün bazında özet. Ürünler sırayla işlendiği için, çalışan taramada henüz bekleyen ilk ürün
    // o an taranandır; "Bekliyor" yerine "Taranıyor" olarak gösterilir
    const scanning = job.status === "running" ? job.keywords.find((item) => item.status === "pending") : undefined;
    const keywordsBody = byId("detail-keywords");
    keywordsBody.replaceChildren();
    for (const item of job.keywords) {
        const statusBadge =
            item === scanning
                ? badge(SCANNING_LABEL, "running")
                : badge(KEYWORD_STATUS_LABELS[item.status] || item.status, item.status);
        keywordsBody.append(
            tableRow(
                [
                    item.keyword,
                    statusBadge,
                    item.top_count,
                    item.rising_count,
                    item.error_message || "",
                ],
                [2, 3],
            ),
        );
    }
    fillKeywordFilter(job.keywords);
    // Ürün özeti çizildi

    byId("job-detail").hidden = false;
    await loadResults();
}

/** Seçili taramanın sonuçlarını formdaki filtrelerle getirir ve tabloyu çizer. */
async function loadResults() {
    const jobId = state.selectedJobId;
    if (jobId === null) {
        return;
    }
    const page = await api(`/jobs/${jobId}/results`, {
        params: {
            type: byId("results-type").value,
            keyword: byId("results-keyword").value,
            min_value: byId("results-min-value").value,
            search: byId("results-search").value.trim(),
            breakout_only: byId("results-breakout").checked,
            limit: RESULTS_PAGE_SIZE,
            offset: state.resultsOffset,
        },
    });
    if (state.selectedJobId !== jobId) {
        return;
    }

    const body = byId("results-body");
    body.replaceChildren();
    for (const item of page.items) {
        body.append(
            tableRow(
                [
                    item.seed_keyword,
                    TYPE_LABELS[item.type] || item.type,
                    item.rank,
                    item.query,
                    formatValue(item.type, item.value),
                    item.is_breakout ? badge("Breakout", "breakout") : "",
                ],
                [2, 4],
            ),
        );
    }

    // Sonuç yoksa nedeni yazılır: tarama henüz sürüyor olabilir veya filtreler hiçbir kaydı tutmuyordur
    let summary = `${formatNumber(page.total)} sonuç`;
    if (page.total === 0) {
        summary = ACTIVE_STATUSES.includes(state.selectedJobStatus)
            ? "Henüz sonuç yok; ürünler tarandıkça burada görünecek."
            : "Bu filtrelerle sonuç yok.";
    }
    byId("results-summary").textContent = summary;
    renderPager(byId("results-pager"), page, (offset) => {
        state.resultsOffset = offset;
        guarded(loadResults);
    });
}

// --- Kendiliğinden yenileme ---

/**
 * Üst şeridi, tarama listesini ve süren seçili taramayı yeniler.
 * Kuyrukta iş olduğu sürece kendini yeniden zamanlar; kuyruk boşalınca durur.
 */
async function tick() {
    clearTimeout(state.pollTimer);
    const health = await refreshHealth();
    await refreshJobs();

    // Seçili tarama sürüyorsa (veya az önce bittiyse) detayını da güncelle
    if (state.selectedJobId !== null && ACTIVE_STATUSES.includes(state.selectedJobStatus)) {
        await refreshSelectedJob();
    }
    // Detay güncel

    // Aynı anda yalnızca bir zamanlayıcı bekler (elle "Yenile" ikinci bir döngü başlatmasın)
    clearTimeout(state.pollTimer);
    if (health.queue.running + health.queue.pending > 0) {
        state.pollTimer = setTimeout(() => guarded(tick), POLL_INTERVAL_MS);
    }
}

// --- Breakout'lar ---

/** Breakout listesini formdaki filtrelerle getirir ve çizer. */
async function loadBreakouts() {
    // Yerel saatle girilen tarih, API'nin beklediği ISO 8601 (UTC) biçimine çevrilir
    const sinceValue = byId("breakouts-since").value;
    const page = await api("/insights/breakouts", {
        params: {
            keyword: byId("breakouts-keyword").value.trim(),
            since: sinceValue ? new Date(sinceValue).toISOString() : "",
            limit: BREAKOUTS_PAGE_SIZE,
            offset: state.breakoutsOffset,
        },
    });

    const body = byId("breakouts-body");
    body.replaceChildren();
    for (const item of page.items) {
        const jobLink = el("button", {
            text: `#${item.job_id}`,
            className: "link",
            attrs: { type: "button" },
            onClick: () => guarded(() => selectJob(item.job_id)),
        });
        body.append(
            tableRow(
                [item.seed_keyword, item.query, formatValue("rising", item.value), jobLink, formatDate(item.scanned_at)],
                [2],
            ),
        );
    }

    byId("breakouts-summary").textContent =
        page.total === 0 ? "Breakout bulunamadı." : `${formatNumber(page.total)} breakout`;
    renderPager(byId("breakouts-pager"), page, (offset) => {
        state.breakoutsOffset = offset;
        guarded(loadBreakouts);
    });
}

// --- Karşılaştırma ---

/** Karşılaştırma seçeneklerini sonuç üretmiş son taramalarla doldurur. */
async function loadDiffOptions() {
    const page = await api("/jobs");
    const jobs = page.items.filter((job) => SUCCESSFUL_STATUSES.includes(job.status));

    const fromSelect = byId("diff-from");
    const toSelect = byId("diff-to");
    const previous = { from: fromSelect.value, to: toSelect.value };
    for (const select of [fromSelect, toSelect]) {
        select.replaceChildren();
        for (const job of jobs) {
            const label = `#${job.id} · ${formatDate(job.created_at)} · ${job.total_keywords} ürün`;
            select.append(el("option", { text: label, attrs: { value: String(job.id) } }));
        }
    }

    // Önceki seçim korunur; yoksa en yeni iki tarama seçilir (liste en yeniden eskiye sıralıdır)
    const ids = jobs.map((job) => String(job.id));
    if (ids.includes(previous.from) && ids.includes(previous.to)) {
        fromSelect.value = previous.from;
        toSelect.value = previous.to;
    } else if (jobs.length >= 2) {
        toSelect.value = ids[0];
        fromSelect.value = ids[1];
    }
    // Seçim tamamlandı

    byId("diff-empty").hidden = jobs.length >= 2;
}

/** Başlıklı bir "yeni giren" veya "çıkan" listesi üretir. */
function diffItemList(title, items, className, sign) {
    const wrapper = el("div");
    wrapper.append(el("p", { text: `${title} (${items.length})`, className: "sub-title" }));
    if (items.length === 0) {
        wrapper.append(el("p", { text: "—", className: "muted" }));
        return wrapper;
    }
    const list = el("ul", { className: "diff-list" });
    for (const item of items) {
        list.append(el("li", { text: `${sign} ${item.query} (${formatNumber(item.value)})`, className }));
    }
    wrapper.append(list);
    return wrapper;
}

/** Değeri veya sırası değişen aramaların tablosunu üretir. */
function diffChangeTable(changes) {
    const wrapper = el("div");
    wrapper.append(el("p", { text: `Değişen (${changes.length})`, className: "sub-title" }));
    if (changes.length === 0) {
        wrapper.append(el("p", { text: "—", className: "muted" }));
        return wrapper;
    }

    const head = el("tr", {}, [
        el("th", { text: "Arama" }),
        el("th", { text: "Değer", className: "num" }),
        el("th", { text: "Fark", className: "num" }),
        el("th", { text: "Sıra", className: "num" }),
    ]);
    const body = el("tbody");
    for (const change of changes) {
        const delta = el("span", {
            text: `${change.delta > 0 ? "+" : ""}${formatNumber(change.delta)}`,
            className: change.delta > 0 ? "added" : change.delta < 0 ? "removed" : "muted",
        });
        body.append(
            tableRow(
                [
                    change.query,
                    `${formatNumber(change.old_value)} → ${formatNumber(change.new_value)}`,
                    delta,
                    `${change.old_rank} → ${change.new_rank}`,
                ],
                [1, 2, 3],
            ),
        );
    }
    wrapper.append(el("div", { className: "table-wrap" }, [el("table", {}, [el("thead", {}, [head]), body])]));
    return wrapper;
}

/** Tek bir türün (top veya rising) farkını üretir. */
function diffTypeSection(title, section) {
    return el("div", {}, [
        el("h3", { text: title }),
        diffItemList("Yeni giren", section.new, "added", "+"),
        diffItemList("Çıkan", section.dropped, "removed", "−"),
        diffChangeTable(section.changed),
    ]);
}

/** Seçilen iki taramayı karşılaştırır ve sonucu çizer. */
async function loadDiff() {
    const diff = await api("/insights/diff", {
        params: {
            from_job: byId("diff-from").value,
            to_job: byId("diff-to").value,
            keyword: byId("diff-keyword").value.trim(),
            type: byId("diff-type").value,
        },
    });

    const output = byId("diff-output");
    output.replaceChildren();

    // Uyarılar ve yalnızca tek taramada bulunan ürünler
    const summary = el("div", { className: "card" });
    summary.append(el("h2", { text: `Tarama #${diff.from_job} → #${diff.to_job}` }));
    for (const warning of diff.warnings) {
        summary.append(el("p", { text: `Uyarı: ${warning}`, className: "warning-box" }));
    }
    if (diff.only_in_from.length > 0) {
        summary.append(el("p", { text: `Yalnızca eski taramada: ${diff.only_in_from.join(", ")}`, className: "muted" }));
    }
    if (diff.only_in_to.length > 0) {
        summary.append(el("p", { text: `Yalnızca yeni taramada: ${diff.only_in_to.join(", ")}`, className: "muted" }));
    }
    if (diff.keywords.length === 0) {
        summary.append(el("p", { text: "İki taramada da başarılı olan ortak ürün yok." }));
    }
    output.append(summary);
    // Özet çizildi

    // Her ortak ürün için ayrı kart
    for (const keyword of diff.keywords) {
        output.append(
            el("div", { className: "card" }, [
                el("h2", { text: keyword.keyword }),
                el("div", { className: "columns" }, [
                    diffTypeSection(TYPE_LABELS.top, keyword.top),
                    diffTypeSection(TYPE_LABELS.rising, keyword.rising),
                ]),
            ]),
        );
    }
    // Ürün kartları çizildi
}

// --- Geçmiş ---

/** Bir taramadaki top veya rising listesinin tablosunu üretir. */
function historyTable(title, type, items) {
    const wrapper = el("div", {}, [el("h3", { text: `${title} (${items.length})` })]);
    if (items.length === 0) {
        wrapper.append(el("p", { text: "—", className: "muted" }));
        return wrapper;
    }
    const body = el("tbody");
    for (const item of items) {
        body.append(
            tableRow(
                [item.rank, item.query, formatValue(type, item.value), item.is_breakout ? badge("Breakout", "breakout") : ""],
                [0, 2],
            ),
        );
    }
    const head = el("tr", {}, [
        el("th", { text: "Sıra", className: "num" }),
        el("th", { text: "Arama" }),
        el("th", { text: "Değer", className: "num" }),
        el("th"),
    ]);
    wrapper.append(el("div", { className: "table-wrap" }, [el("table", {}, [el("thead", {}, [head]), body])]));
    return wrapper;
}

/** Bir ürünün tarama geçmişini getirir ve her taramayı ayrı kart olarak çizer. */
async function loadHistory() {
    const keyword = byId("history-keyword").value.trim();
    if (keyword === "") {
        throw new Error("Bir ürün adı yazın");
    }
    const output = byId("history-output");
    output.replaceChildren();

    // Ürün adı yolun parçasıdır; özel karakterler kodlanır
    const history = await api(`/keywords/${encodeURIComponent(keyword)}/history`, {
        params: { type: byId("history-type").value },
    });

    for (const scan of history.scans) {
        const jobLink = el("button", {
            text: `Tarama #${scan.job_id}`,
            className: "link",
            attrs: { type: "button" },
            onClick: () => guarded(() => selectJob(scan.job_id)),
        });
        const meta = `${formatDate(scan.scanned_at)}  ·  Ülke: ${formatGeo(scan.geo)}  ·  ` +
            `Aralık: ${formatTimeframe(scan.timeframe)}`;
        output.append(
            el("div", { className: "card" }, [
                el("div", { className: "card-head" }, [el("h2", { text: history.keyword }), jobLink]),
                el("p", { text: meta, className: "muted" }),
                el("div", { className: "columns" }, [
                    historyTable(TYPE_LABELS.top, "top", scan.top),
                    historyTable(TYPE_LABELS.rising, "rising", scan.rising),
                ]),
            ]),
        );
    }
}

// --- Sekmeler ---

/** Bir sekmeye ilk girişte veya her girişte yüklenecek veriler. */
const VIEW_LOADERS = {
    breakouts: loadBreakouts,
    diff: loadDiffOptions,
};

/** İstenen bölümü gösterir, diğerlerini gizler. */
function showView(name) {
    for (const view of document.querySelectorAll(".view")) {
        view.hidden = view.id !== `view-${name}`;
    }
    for (const tab of document.querySelectorAll(".tab")) {
        tab.classList.toggle("active", tab.dataset.view === name);
    }
}

/** Sekmeye geçer ve o bölümün verisini yükler. */
function openView(name) {
    hideNotice();
    showView(name);
    const loader = VIEW_LOADERS[name];
    if (loader) {
        guarded(loader);
    }
}

// --- Başlangıç ---

/** Form gönderimini sayfayı yenilemeden verilen işleme bağlar. */
function onSubmit(formId, action) {
    byId(formId).addEventListener("submit", (event) => {
        event.preventDefault();
        hideNotice();
        guarded(action);
    });
}

/** Ülke seçimini doldurur: önce en sık seçilen ülke, sonra diğerleri Türkçe ada göre sıralı. */
function fillCountryOptions() {
    const countries = COUNTRY_CODES.filter((code) => code !== PINNED_COUNTRY_CODE)
        .map((code) => ({ code, name: countryName(code) }))
        .sort((a, b) => a.name.localeCompare(b.name, LOCALE));
    countries.unshift({ code: PINNED_COUNTRY_CODE, name: countryName(PINNED_COUNTRY_CODE) });

    const select = byId("job-geo");
    for (const country of countries) {
        select.append(el("option", { text: `${country.name} (${country.code})`, attrs: { value: country.code } }));
    }
}

/** Olayları bağlar ve ilk veriyi yükler. */
function init() {
    fillCountryOptions();
    for (const tab of document.querySelectorAll(".tab")) {
        tab.addEventListener("click", () => openView(tab.dataset.view));
    }

    onSubmit("job-form", createJob);
    onSubmit("results-form", () => {
        state.resultsOffset = 0;
        return loadResults();
    });
    onSubmit("breakouts-form", () => {
        state.breakoutsOffset = 0;
        return loadBreakouts();
    });
    onSubmit("diff-form", loadDiff);
    onSubmit("history-form", loadHistory);

    byId("jobs-refresh").addEventListener("click", () => guarded(tick));
    byId("results-reset").addEventListener("click", () => {
        resetResultFilters();
        guarded(loadResults);
    });

    // Süren taramada geçen süre her saniye yenilenir
    setInterval(renderActivityTiming, ELAPSED_TICK_MS);

    guarded(tick);
}

document.addEventListener("DOMContentLoaded", init);
