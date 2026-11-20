FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Tesseract OCR engine for scanned invoices
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY static ./static
COPY data/reference ./data/reference
COPY samples ./samples

RUN useradd --create-home appuser
USER appuser

# Runtime data (SQLite + uploads) lives on the container's ephemeral disk.
ENV DATA_DIR=/tmp/ap-agent \
    PORT=8000
EXPOSE 8000

# One worker: runs execute in background threads of a single process sharing one SQLite file.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
