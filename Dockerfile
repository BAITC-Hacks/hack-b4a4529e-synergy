FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache

WORKDIR /app

COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt \
    && python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')" \
    && useradd --create-home --uid 10001 app \
    && mkdir -p /app/data \
    && chown app:app /app/data

COPY --chown=app:app app/ app/
COPY --chown=app:app static/ static/
COPY --chown=app:app download_ekt.py .

USER app
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
