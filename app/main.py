from __future__ import annotations

from typing import Annotated
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .models import Job, JobCreate, Workflow, WorkflowCreate
from .scheduler import Scheduler


scheduler = Scheduler()


# Dependency: extract user ID from header
async def get_user_id(
    x_user_id: Annotated[str | None, Header(alias="X-User-ID")] = None,
) -> str:
    if not x_user_id:
        raise HTTPException(status_code=400, detail="X-User-ID header is required")
    return x_user_id


# -----------------------------------------------------------
# Create app first
# -----------------------------------------------------------
app = FastAPI(title="InstanSeg Workflow Scheduler", version="0.1.0")

# -----------------------------------------------------------
# Add CORS middleware AFTER app is created
# -----------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],    # Your React dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------------------------------------
# Startup / Shutdown
# -----------------------------------------------------------
@app.on_event("startup")
async def _startup() -> None:
    await scheduler.start()

@app.on_event("shutdown")
async def _shutdown() -> None:
    await scheduler.stop()


# -----------------------------------------------------------
# Workflow endpoints
# -----------------------------------------------------------
@app.post("/workflows", response_model=Workflow)
async def create_workflow(
    payload: WorkflowCreate, user_id: str = Depends(get_user_id)
) -> Workflow:
    return await scheduler.create_workflow(user_id, payload.name)


@app.get("/workflows", response_model=list[Workflow])
async def list_workflows(user_id: str = Depends(get_user_id)) -> list[Workflow]:
    return await scheduler.list_workflows_for_user(user_id)


@app.get("/workflows/{workflow_id}/jobs", response_model=list[Job])
async def list_jobs(
    workflow_id: str, user_id: str = Depends(get_user_id)
) -> list[Job]:
    return await scheduler.list_jobs_for_workflow(user_id, workflow_id)


# -----------------------------------------------------------
# Job endpoints
# -----------------------------------------------------------
@app.post("/jobs", response_model=Job)
async def create_job(
    payload: JobCreate, user_id: str = Depends(get_user_id)
) -> Job:
    return await scheduler.enqueue_job(user_id, payload)


@app.post("/jobs/{job_id}/cancel", response_model=Job)
async def cancel_job(job_id: str, user_id: str = Depends(get_user_id)) -> Job:
    try:
        return await scheduler.cancel_job(user_id, job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Job not found")


@app.get("/jobs/{job_id}", response_model=Job)
async def get_job(job_id: str, user_id: str = Depends(get_user_id)) -> Job:
    job = await scheduler.store.get_job(job_id)
    if job is None or job.user_id != user_id:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/jobs/{job_id}/result")
async def download_result(job_id: str, user_id: str = Depends(get_user_id)):
    job = await scheduler.store.get_job(job_id)
    if job is None or job.user_id != user_id:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.result_path:
        raise HTTPException(status_code=404, detail="Result not ready")
    return FileResponse(job.result_path, filename=f"{job_id}.json")