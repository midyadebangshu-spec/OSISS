#!/bin/sh
# Container start: fetch models if asked, initialise the schema if asked, then run the API.
set -eu
cd /app

if [ -n "${MODELS_S3_URI:-}" ]; then
  python src/sync_models.py "$MODELS_S3_URI"
elif [ "${DOWNLOAD_MODELS:-false}" = "true" ]; then
  python src/download_models.py
fi

# Run on one container only (the ingestion worker): creates tables and the Elasticsearch index. Idempotent.
if [ "${RUN_DB_INIT:-false}" = "true" ]; then
  python src/db_init.py
fi

# FORWARDED_ALLOW_IPS: addresses of the load balancer / proxy whose X-Forwarded-For is trusted for client IPs
# (rate limits, admin lockout, ADMIN_ALLOWED_IPS). Only set "*" when nothing else can reach the container.
exec python -m uvicorn src.api_server:app \
  --host "${API_HOST:-0.0.0.0}" --port "${API_PORT:-8000}" \
  --proxy-headers --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}"
