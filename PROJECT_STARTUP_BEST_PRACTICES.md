# PrismaSpace Project Startup Best Practices

This document defines a practical startup standard for development and production environments.

## 1. Runtime Architecture (Must-Have Processes)

PrismaSpace backend is not a single-process app. A complete runtime needs:

1. `Web API` process (FastAPI/Uvicorn or Gunicorn+UvicornWorker)
2. `Worker` process (ARQ consumer for background jobs)
3. `Redis` (cache + ARQ queue broker)
4. `PostgreSQL` (main DB + tenant/test DB as needed)
5. `Vector DB` (Milvus/Qdrant based on `.env`)
6. `Tika` server (document parsing pipeline)

Non-negotiable rule:
- If `worker` is not running, queued background tasks will not execute.

## 2. Preflight Checklist

Before starting any environment:

1. Python version is `>=3.10.12,<3.13`
2. `poetry install` completed
3. `.env` created (copy from `.env.example` and fill secrets/endpoints)
4. PostgreSQL/Redis/VectorDB/Tika are reachable from app host
5. DB migration is up to date

Recommended bootstrapping:

```bash
poetry install
poetry run alembic upgrade head
poetry run python scripts/seed_initial_data.py
```

## 3. Development Best Practice

### 3.1 Preferred Process Model

Run both web and worker locally:

```bash
poetry run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
poetry run arq app.worker.WorkerSettings --watch src
```

Or use the provided scripts:

```powershell
.\scripts\start-dev.ps1
.\scripts\stop-dev.ps1
```

What `start-dev.ps1` does:

1. Optional migration (default enabled)
2. Starts web and worker as background processes
3. Writes PID files and logs under `logs/`

### 3.2 Dev Guardrails

1. Keep web + worker on the same commit to avoid schema/task signature drift.
2. Run migration before restarting worker after task payload changes.
3. Watch logs for queue failures:
   - `logs/dev-web.out.log` / `logs/dev-web.err.log`
   - `logs/dev-worker.out.log` / `logs/dev-worker.err.log`

## 4. Production Best Practice

### 4.1 Process Strategy

Use dedicated process supervision (systemd/supervisor/k8s).

Recommended split:

1. Web: Gunicorn + Uvicorn workers
2. Worker: one or more ARQ worker processes

Provided production script examples:

```bash
./scripts/start-prod-web.sh
./scripts/start-prod-worker.sh
```

Do not use `--reload` in production.

### 4.2 Release Sequence (Safe Order)

1. Deploy code and environment file
2. Run DB migration once:
   - `poetry run alembic upgrade head`
3. Restart worker processes
4. Restart web processes
5. Verify logs and queue drain

Why this order:
- Worker/web should run on migrated schema and same code version.

### 4.3 Capacity Baseline

Start with:

1. Web workers: `WEB_CONCURRENCY=2~4` (adjust by CPU and latency)
2. ARQ workers: at least `1`, scale up for queue backlog/slow tasks

Scale signals:

1. Redis queue length keeps growing
2. Task completion latency exceeds SLO
3. CPU saturation on worker hosts

## 5. Operations Checklist

After startup:

1. Confirm web process exists
2. Confirm worker process exists
3. Confirm worker logs show startup message
4. Trigger one background task (e.g. knowledge processing) and confirm it is consumed
5. Confirm no migration errors in logs

Useful checks:

```bash
poetry run arq --check app.worker.WorkerSettings
```

```bash
redis-cli ping
```

## 6. Security and Reliability Baseline

1. Keep `.env` out of git and restrict file permission.
2. Do not expose Redis/PostgreSQL directly to public network.
3. Put web service behind reverse proxy/load balancer.
4. Enable log rotation for web and worker logs.
5. Use health checks and auto-restart policy in process manager.

## 7. Current Codebase Notes

1. Background tasks are enqueued from web process via `arq_pool.enqueue_job(...)`.
2. ARQ task consumer entry is `app.worker.WorkerSettings`.
3. Cron jobs should be validated before relying on periodic execution in production (task registration must mutate the shared worker registry at import time).
