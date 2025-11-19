from __future__ import annotations

import asyncio
from typing import Dict, List, Optional

from .models import Job, Workflow


class InMemoryStore:
    """
    Simple in-memory store.
    For this take-home it's enough; in real life you'd plug Redis / DB here.
    """

    def __init__(self) -> None:
        self._workflows: Dict[str, Workflow] = {}
        self._jobs: Dict[str, Job] = {}
        # workflow_id -> [job_id, ...] (FIFO order)
        self._workflow_jobs: Dict[str, List[str]] = {}
        self._lock = asyncio.Lock()

    # ----------------- Workflow ops -----------------

    async def add_workflow(self, wf: Workflow) -> None:
        async with self._lock:
            self._workflows[wf.workflow_id] = wf
            self._workflow_jobs.setdefault(wf.workflow_id, [])

    async def list_workflows_for_user(self, user_id: str) -> List[Workflow]:
        async with self._lock:
            return [
                wf for wf in self._workflows.values()
                if wf.user_id == user_id
            ]

    async def get_workflow(self, workflow_id: str) -> Optional[Workflow]:
        async with self._lock:
            return self._workflows.get(workflow_id)

    async def update_workflow(self, wf: Workflow) -> None:
        async with self._lock:
            self._workflows[wf.workflow_id] = wf

    async def attach_job_to_workflow(self, workflow_id: str, job_id: str) -> None:
        async with self._lock:
            self._workflow_jobs.setdefault(workflow_id, []).append(job_id)

    async def list_jobs_for_workflow(self, workflow_id: str) -> List[Job]:
        """
        Return all jobs for a workflow in FIFO order.

        """
        async with self._lock:
            job_ids = list(self._workflow_jobs.get(workflow_id, []))

        jobs: List[Job] = []
        for jid in job_ids:
            j = await self.get_job(jid)
            if j is not None:
                jobs.append(j)
        return jobs

    # ----------------- Job ops -----------------

    async def add_job(self, job: Job) -> None:
        async with self._lock:
            self._jobs[job.job_id] = job

    async def get_job(self, job_id: str) -> Optional[Job]:
        async with self._lock:
            return self._jobs.get(job_id)

    async def update_job(self, job: Job) -> None:
        async with self._lock:
            self._jobs[job.job_id] = job

    async def all_jobs(self) -> List[Job]:
        async with self._lock:
            return list(self._jobs.values())