#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f ".env" ]; then
  echo "Missing .env in project root." >&2
  exit 1
fi

exec poetry run arq app.worker.WorkerSettings

