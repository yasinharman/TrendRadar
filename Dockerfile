# TrendRadar API imajı
FROM python:3.12-slim

# Loglar anında görünsün, konteynere .pyc dosyası yazılmasın
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Bağımlılıklar ayrı katmanda kurulur: kod değişse de bu katman önbellekten gelir
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Uygulama root yetkisiyle çalışmaz; çalışma dizini bu kullanıcıya aittir
# (PostgreSQL yerine varsayılan SQLite kullanılırsa veritabanı dosyası buraya yazılır)
RUN useradd --create-home --uid 1000 appuser && chown appuser:appuser /app
COPY --chown=appuser:appuser app ./app
USER appuser

EXPOSE 8000

# Tek süreç bilinçli bir tercihtir: tarama kuyruğunun kilidi süreç içindedir;
# birden çok worker (--workers) aynı anda tarama başlatır ve Google 429 döner
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
