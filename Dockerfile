# River Personality Monitor — Cloud Run container
# python:3.12-slim (NOT alpine: we need manylinux wheels + the Python stdlib
# layout that the application's C-extension deps (duckdb, pyarrow, polars)
# already ship). Data is NOT baked into the image: it is fetched from GCS at
# container startup by cloud_boot.py (ADC default credentials).

FROM python:3.12-slim

# Avoid .pyc bytecode in the read-only layers and keep stdout unbuffered so
# gunicorn/access logs stream to Cloud Run's log console promptly.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    MODE=service

WORKDIR /app

# 1) Copy dependency manifest first for Docker layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 2) Copy application source (data/ is excluded via .dockerignore — never
#    baked into the image; fetched from GCS at startup).
COPY . .

# Entrypoint: MODE=service -> sync GCS data (if GCS_BUCKET set) then serve;
# MODE=job -> run the Cloud Run job update (handles its own GCS writeback).
EXPOSE 8080

CMD ["sh", "-c", "if [ -n \"$GCS_BUCKET\" ]; then python3 cloud_boot.py; fi; \
  if [ \"$MODE\" = \"service\" ]; then \
  exec gunicorn app:server --workers 1 --threads 8 --bind 0.0.0.0:${PORT:-8080} --timeout 300 --graceful-timeout 10 --access-logfile -; \
else \
  exec python3 update_data.py; \
fi"]
