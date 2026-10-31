# OSISS API image (search + admin API + optional ingestion worker + the built website).
#
#   docker build -t osiss-api .
#   docker run --gpus all -p 8000:8000 -v $PWD/models:/app/models:ro --env-file .env osiss-api
#
# GPU: PyTorch from PyPI bundles the CUDA runtime; the host needs the NVIDIA driver and the NVIDIA Container Toolkit.
# Models are not baked in (about 12 GB): mount them at /app/models, or set MODELS_S3_URI to fetch them at start.

FROM node:22-bookworm-slim AS website
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng tesseract-ocr-ben tesseract-ocr-hin curl \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY src/ src/
COPY deploy/docker-entrypoint.sh /usr/local/bin/osiss-entrypoint
COPY --from=website /build/dist frontend/dist
RUN useradd --create-home --uid 10001 osiss \
 && mkdir -p data/pdfs data/uploads data/admin models \
 && chown -R osiss:osiss /app \
 && chmod 755 /usr/local/bin/osiss-entrypoint
USER osiss

# Behind a load balancer: serve the website, load the models before reporting ready, share state via PostgreSQL.
ENV API_HOST=0.0.0.0 \
    API_PORT=8000 \
    SERVE_FRONTEND=true \
    WARMUP_ON_START=true
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=300s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${API_PORT}/ready" || exit 1
ENTRYPOINT ["osiss-entrypoint"]
