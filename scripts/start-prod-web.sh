#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f ".env" ]; then
  echo "Missing .env in project root." >&2
  exit 1
fi

APP_HOST="${APP_HOST:-0.0.0.0}"
APP_PORT="${APP_PORT:-8000}"
WEB_CONCURRENCY="${WEB_CONCURRENCY:-4}"
GUNICORN_TIMEOUT="${GUNICORN_TIMEOUT:-120}"
KEEPALIVE="${KEEPALIVE:-5}"

exec poetry run gunicorn app.main:app \
  -k uvicorn.workers.UvicornWorker \
  --bind "${APP_HOST}:${APP_PORT}" \
  --workers "${WEB_CONCURRENCY}" \
  --timeout "${GUNICORN_TIMEOUT}" \
  --keep-alive "${KEEPALIVE}" \
  --access-logfile - \
  --error-logfile -

