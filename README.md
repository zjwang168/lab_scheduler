# lab_scheduler

Branch-aware, multi-tenant scheduler for Whole Slide Image (WSI) inference workloads powered by FastAPI, asyncio workers, and InstanSeg.

## Table of Contents
1. [Architecture Overview](#architecture-overview)
2. [Quick Start](#quick-start)
3. [API Summary](#api-summary)
4. [Frontend Dashboard](#frontend-dashboard)
5. [Workflow & Job Semantics](#workflow--job-semantics)
6. [Testing](#testing)
7. [Monitoring & Metrics](#monitoring--metrics)
8. [Scaling to 10× Load](#scaling-to-10-load)
9. [Result Artifacts](#result-artifacts)
10. [Future Enhancements](#future-enhancements)

## Architecture Overview
- **FastAPI backend (`app/`)**: exposes workflow/job APIs, enforces branch FIFO and multi-tenant limits, and updates Prometheus metrics.
- **Scheduler core (`app/scheduler.py`)**: maintains pending queue, rate limiting, per-branch locks, and triggers worker tasks.
- **Worker layer (`app/workers.py`)**: runs InstanSeg tiling pipelines and tissue masking while streaming progress updates back to the store.
- **React frontend (`frontend/`)**: polls APIs for real-time progress and provides download links for completed jobs.
- **Docker Compose**: spins up API and frontend for local demos (extend with Redis / Prometheus as needed).

## Quick Start

### Local Environment
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload

# Frontend
cd frontend
npm install
npm run dev
```
- Backend: http://localhost:8000 (Swagger UI at `/docs`)
- Frontend: http://localhost:5173
- Download sample WSI (e.g., `CMU-1.svs`) from the [CMU OpenSlide dataset](https://openslide.cs.cmu.edu/download/openslide-testdata/Aperio/).

### Docker Compose
```bash
docker compose up --build
```

## API Summary
All endpoints require `X-User-ID` header.

| Method | Path | Description |
|--------|------|-------------|
| POST | `/workflows` | Create workflow `{ "name": "My WSI" }` |
| GET | `/workflows` | List workflows for current tenant |
| GET | `/workflows/{workflow_id}/jobs` | List jobs for a workflow |
| POST | `/jobs` | Enqueue job (workflow, branch, job_type, image_path, params) |
| POST | `/jobs/{job_id}/cancel` | Cancel while pending |
| GET | `/jobs/{job_id}` | Inspect job state/progress |
| GET | `/jobs/{job_id}/result` | Download generated JSONL/PNG |

Swagger / OpenAPI spec: `http://localhost:8000/docs`.

## Frontend Dashboard
- `frontend/src/App.tsx` provides:
  - Tenant switcher using `localStorage`.
  - Workflow creation + progress cards.
  - Job table showing branch, type, state, progress bars, and download links.
- Polls backend every 1.5 seconds for near real-time updates. Replace with WebSockets for lower latency.

## Workflow & Job Semantics
- **Branch-aware FIFO**: only one job per `(user_id, branch_id)` runs at a time.
- **Multi-tenant limit**: at most `MAX_ACTIVE_USERS` tenants can have running jobs concurrently; extras wait in queue.
- **Global worker cap**: `MAX_WORKERS` semaphore protects the InstanSeg worker pool.
- **Job states**: `PENDING → RUNNING → SUCCEEDED/FAILED` (or `CANCELLED` before execution).
- **Progress tracking**: workers save fractional progress per tile, enabling accurate workflow-level progress aggregation.
- **Result delivery**: segmentation jobs output JSONL polygons; tissue mask jobs emit PNG masks.

## Testing
- `pytest` covers scheduler behavior:
  1. Branch FIFO serialization.
  2. Active-user limit enforcement.
  3. Pending job cancellation removing queue entries.

Run tests:
```bash
pytest
```

## Monitoring & Metrics
- Exposes Prometheus metrics via `prometheus_client`:
  - `scheduler_jobs_enqueued_total{user_id,job_type}`
  - `scheduler_jobs_completed_total{user_id,job_type,state}`
  - `scheduler_pending_jobs`
  - `scheduler_active_users`
  - `scheduler_rate_limited_total{user_id}`
- Hook Prometheus to `/metrics`, then visualize in Grafana (queue depth, per-branch latency, active users, drops, etc.).

## Scaling to 10× Load
1. **Durable store + queue**: Replace `InMemoryStore` with Redis/Postgres and use Redis Streams / RabbitMQ for job queueing.
2. **Dedicated workers**: Run `workers.py` inside autoscaled worker pods (Celery/Dramatiq) and decouple from API pods.
3. **Sharding**: Partition by tenant or branch key to remove hot-spot contention.
4. **GPU-aware scheduling**: Track GPU availability and enforce per-GPU concurrency for InstanSeg pipelines.
5. **Distributed rate limiting**: Use Redis token buckets to coordinate across replicas.
6. **Caching & re-use**: Share intermediate tiles, tissue masks, and slide metadata to reduce redundant work.
7. **Observability**: Expand dashboards with job latency histograms, queue depth per tenant, and alerting (e.g., queue depth > threshold).

## Result Artifacts
- Default output directory: `results/<user_id>/`.
- Cell segmentation jobs write `<job_id>_cells.jsonl` (one polygon per line with metadata like tile origin, pixel size).
- Tissue mask jobs write `<job_id>_tissue_mask.png`.
- Download via API or inspect the filesystem for debugging.

## Future Enhancements
- WebSocket or SSE stream for immediate job updates.
- Result visualization overlay (reference TissueLab for interactive cell inspection).
- Redis-backed queue + background worker service in `docker-compose`.
- Prometheus + Grafana services baked into compose with sample dashboards.
- CI pipeline (lint, tests, frontend type-check, docker build).