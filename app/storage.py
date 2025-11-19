from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Dict, List, Optional

from .models import Job, JobCreate, JobState, Workflow, WorkflowCreate, WorkflowStatus

import uuid


class Store:
    """
    Very small in-memory store.

    In a real system this would be backed by Postgres / Redis.
    """

    def __init__(self) -> None:
        self._workflows: Dict[str, Workflow] = {}
        self._jobs: Dict[str, Job] = {}
        # workflow_id -> list[job_id] (creation order)
        self._workflow_jobs: Dict[str, List[str]] = {}

        self._lock = asyncio.Lock()

    # ---------- Workflow APIs ----------

    async def create_workflow(self, user_id: str, payload: WorkflowCreate) -> Workflow:
        async with self._lock:
            wid = str(uuid.uuid4())
            wf = Workflow(
                workflow_id=wid,
                user_id=user_id,
                name=payload.name,
                created_at=datetime.utcnow(),
                status=WorkflowStatus.PENDING,
                progress=0.0,
                branches={},
            )
            self._workflows[wid] = wf
            self._workflow_jobs.setdefault(wid, [])
            return wf

    async def get_workflow(self, workflow_id: str) -> Optional[Workflow]:
        async with self._lock:
            return self._workflows.get(workflow_id)

    async def update_workflow(self, wf: Workflow) -> Workflow:
        async with self._lock:
            self._workflows[wf.workflow_id] = wf
            return wf

    async def list_workflows_for_user(self, user_id: str) -> List[Workflow]:
        async with self._lock:
            return [w for w in self._workflows.values() if w.user_id == user_id]

    # ---------- Job APIs ----------

    async def create_job(self, user_id: str, payload: JobCreate) -> Job:
        async with self._lock:
            jid = str(uuid.uuid4())
            job = Job(
                job_id=jid,
                user_id=user_id,
                workflow_id=payload.workflow_id,
                branch_id=payload.branch_id,
                job_type=payload.job_type,
                image_path=payload.image_path,
                params=payload.params or {},
                state=JobState.PENDING,
                progress=0.0,
                created_at=datetime.utcnow(),
                started_at=None,
                finished_at=None,
                error=None,
                result_path=None,
            )
            self._jobs[jid] = job
            self._workflow_jobs.setdefault(payload.workflow_id, []).append(jid)
            return job

    async def get_job(self, job_id: str) -> Optional[Job]:
        async with self._lock:
            return self._jobs.get(job_id)

    async def update_job(self, job: Job) -> Job:
        async with self._lock:
            self._jobs[job.job_id] = job
            return job

    async def list_jobs_for_workflow(
        self, user_id: str, workflow_id: str
    ) -> List[Job]:
        """Used by API – user filtered."""
        async with self._lock:
            ids = self._workflow_jobs.get(workflow_id, [])
            return [
                self._jobs[jid]
                for jid in ids
                if self._jobs[jid].user_id == user_id
            ]

    async def list_jobs_for_workflow_id(self, workflow_id: str) -> List[Job]:
        """Internal helper – no user filter."""
        async with self._lock:
            ids = self._workflow_jobs.get(workflow_id, [])
            return [self._jobs[jid] for jid in ids]

    async def set_job_state(
        self,
        job_id: str,
        state: JobState,
        *,
        progress: Optional[float] = None,
        error: Optional[str] = None,
        started_at: Optional[datetime] = None,
        finished_at: Optional[datetime] = None,
        result_path: Optional[str] = None,
    ) -> Optional[Job]:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None

            data = job.model_dump()
            data["state"] = state
            if progress is not None:
                data["progress"] = progress
            if error is not None:
                data["error"] = error
            if started_at is not None:
                data["started_at"] = started_at
            if finished_at is not None:
                data["finished_at"] = finished_at
            if result_path is not None:
                data["result_path"] = result_path

            job = Job(**data)
            self._jobs[job_id] = job
            return job