FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    AUDIT_MODE=offline

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
       tesseract-ocr \
       tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md LICENSE MANIFEST.in ./
COPY src ./src
COPY data ./data
COPY evals/cases ./evals/cases
COPY evals/baseline ./evals/baseline
COPY web ./web
RUN python -m pip install .

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/runs \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3)"

CMD ["uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
