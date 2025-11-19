from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime
from typing import Deque, Dict, List, Optional, Set, Tuple

from .config import settings
from .models import (
    Job,
    JobCreate,
    JobState,
    Workflow,
    WorkflowCreate,
    WorkflowStatus,
)
from .storage import Store


# One logical “branch” is defined by (user, workflow, branch_id)
BranchKey = Tuple[str, str, str]  # (user_id, workflow_id, branch_id)


class Scheduler:
    """
    Branch-aware, multi-tenant scheduler with:

    - Per-branch FIFO queues:
        jobs in the same (user, workflow, branch_id) run serially.

    - Parallelism across branches up to MAX_WORKERS:
        different branches can run at the same time, limited by worker pool.

    - Global active-user limit (MAX_ACTIVE_USERS):
        at most N distinct users can have RUNNING jobs at once.
        Extra users' jobs stay in PENDING until a slot opens.

    This class is intentionally self-contained so it can later be swapped out
    for a Redis-backed or distributed implementation.
    """

    def __init__(self) -> None:
        # Persistence (in-memory for this take-home)
        self.store = Store()

        # Global ready queue: job_ids that are at the *head* of their branch
        # and eligible to be scheduled, subject to user & worker limits.
        self._ready_queue: asyncio.Queue[str] = asyncio.Queue()

        # Per-branch FIFO queues: (user, workflow, branch) -> deque[job_id]
        self._branch_queues: Dict[BranchKey, Deque[str]] = {}

        # Worker pool
        self._max_workers: int = settings.MAX_WORKERS
        self._worker_tasks: List[asyncio.Task] = []
        self._running: bool = False

        # Active-user accounting
        self._max_active_users: int = settings.MAX_ACTIVE_USERS
        self._active_users: Set[str] = set()  # users that currently have RUNNING jobs
        self._user_running_counts: Dict[str, int] = {}  # user_id -> #running jobs

        # Lock protecting _branch_queues
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    # Lifecycle (called from FastAPI startup/shutdown)
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        """Start worker tasks."""
        if self._running:
            return
        self._running = True
        self._worker_tasks = [
            asyncio.create_task(self._worker_loop(i))
            for i in range(self._max_workers)
        ]

    async def stop(self) -> None:
        """Gracefully stop all workers."""
        self._running = False
        for t in self._worker_tasks:
            t.cancel()
        await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        self._worker_tasks = []

    # ------------------------------------------------------------------ #
    # Public API used by FastAPI routes
    # ------------------------------------------------------------------ #

    async def create_workflow(self, user_id: str, name: str) -> Workflow:
        wf = await self.store.create_workflow(user_id, WorkflowCreate(name=name))
        return wf

    async def list_workflows_for_user(self, user_id: str) -> List[Workflow]:
        return await self.store.list_workflows_for_user(user_id)

    async def list_jobs_for_workflow(
        self, user_id: str, workflow_id: str
    ) -> List[Job]:
        return await self.store.list_jobs_for_workflow(user_id, workflow_id)

    async def enqueue_job(self, user_id: str, payload: JobCreate) -> Job:
        """
        Create a PENDING job and enqueue it at the tail of its branch FIFO.

        If this is the first job in the branch, it becomes the head and is
        pushed into the global ready queue.
        """
        # 1) Persist job in store
        job = await self.store.create_job(user_id, payload)
        key: BranchKey = (user_id, payload.workflow_id, payload.branch_id)

        # 2) Add to per-branch queue
        async with self._lock:
            dq = self._branch_queues.setdefault(key, deque())
            dq.append(job.job_id)

            # First job in branch → eligible to run when worker/user limits allow
            if len(dq) == 1:
                await self._ready_queue.put(job.job_id)

        # Ensure workflow progress is computed (still 0 but keeps logic consistent)
        await self._update_workflow_progress(job.workflow_id)
        return job

    async def cancel_job(self, user_id: str, job_id: str) -> Job:
        """
        Cancel a job that is still QUEUED (PENDING).

        If it is at the head of the branch queue, the next job in that branch
        will be scheduled.
        """
        job = await self.store.get_job(job_id)
        if job is None or job.user_id != user_id:
            raise ValueError("Job not found")

        # Allow cancelling queued jobs only
        if job.state is not JobState.PENDING:
            return job

        # Mark as CANCELLED
        job = await self.store.set_job_state(
            job_id,
            JobState.CANCELLED,
            finished_at=datetime.utcnow(),
            progress=0.0,
        )

        key: BranchKey = (job.user_id, job.workflow_id, job.branch_id)

        # Remove from branch queue; maybe promote next job
        async with self._lock:
            dq = self._branch_queues.get(key)
            if dq and job_id in dq:
                was_head = dq[0] == job_id
                dq.remove(job_id)

                if was_head and dq:
                    # next head-of-branch becomes schedulable
                    await self._ready_queue.put(dq[0])

                if not dq:
                    self._branch_queues.pop(key, None)

        await self._update_workflow_progress(job.workflow_id)
        return job

    # ------------------------------------------------------------------ #
    # Worker loop
    # ------------------------------------------------------------------ #

    async def _worker_loop(self, worker_id: int) -> None:
        """
        Worker:

        - Waits for a job_id from the global ready queue (branch head).
        - Enforces active-user limit.
        - Runs the job (fake image processing for now).
        - When done, promotes next job in the same branch (if any).
        """
        while self._running:
            try:
                job_id = await self._ready_queue.get()
            except asyncio.CancelledError:
                break

            job = await self.store.get_job(job_id)
            if job is None:
                self._ready_queue.task_done()
                continue

            # Job may have been cancelled while queued
            if job.state is not JobState.PENDING:
                self._ready_queue.task_done()
                continue

            user_id = job.user_id

            # Enforce "at most MAX_ACTIVE_USERS have RUNNING jobs"
            while True:
                if user_id in self._active_users:
                    break  # this user already active, okay
                if len(self._active_users) < self._max_active_users:
                    break  # free slot, okay
                # No slot: temporarily wait and re-check
                await asyncio.sleep(0.05)

            # Mark user active / increment running count
            self._active_users.add(user_id)
            self._user_running_counts[user_id] = (
                self._user_running_counts.get(user_id, 0) + 1
            )

            # Transition job → RUNNING
            job = await self.store.set_job_state(
                job_id,
                JobState.RUNNING,
                started_at=datetime.utcnow(),
            )

            self._ready_queue.task_done()

            try:
                # Execute long-running job (simulated for now)
                await self._execute_job(job)

                # If still RUNNING (i.e. not failed/cancelled), mark SUCCEEDED
                latest = await self.store.get_job(job_id)
                if latest and latest.state is JobState.RUNNING:
                    await self.store.set_job_state(
                        job_id,
                        JobState.SUCCEEDED,
                        progress=1.0,
                        finished_at=datetime.utcnow(),
                    )
            except Exception as exc:  # noqa: BLE001
                await self.store.set_job_state(
                    job_id,
                    JobState.FAILED,
                    error=str(exc),
                    finished_at=datetime.utcnow(),
                )
            finally:
                # Branch + workflow bookkeeping
                job_end = await self.store.get_job(job_id)
                if job_end:
                    await self._on_job_complete(job_end)

                # Decrement per-user running count; maybe free user slot
                self._user_running_counts[user_id] -= 1
                if self._user_running_counts[user_id] <= 0:
                    self._user_running_counts.pop(user_id, None)
                    self._active_users.discard(user_id)

    # ------------------------------------------------------------------ #
    # Job execution + bookkeeping
    # ------------------------------------------------------------------ #

    async def _execute_job(self, job: Job) -> None:
        """
        Fake long-running image job.

        This is where InstanSeg integration will plug in later:
        - open WSI
        - tile into patches
        - run model
        - merge polygons
        - write JSON/H5/etc.

        For now we simulate tile processing with 10 steps and sleep.
        """
        steps = 10
        for i in range(1, steps + 1):
            await asyncio.sleep(0.3)  # simulate compute

            progress = i / steps
            # Update job progress
            await self.store.set_job_state(
                job.job_id,
                JobState.RUNNING,
                progress=progress,
            )
            # Recompute workflow progress
            await self._update_workflow_progress(job.workflow_id)

    async def _on_job_complete(self, job: Job) -> None:
        """
        Called when a job reaches a terminal state.

        - Pops it from the head of its branch queue.
        - If there is a next job in that branch, push it into ready_queue.
        """
        key: BranchKey = (job.user_id, job.workflow_id, job.branch_id)

        async with self._lock:
            dq = self._branch_queues.get(key)
            if not dq:
                return

            # Job should be at head of queue
            if dq and dq[0] == job.job_id:
                dq.popleft()

            if dq:
                # Promote next job in this branch
                await self._ready_queue.put(dq[0])
            else:
                # Branch empty → remove
                self._branch_queues.pop(key, None)

        await self._update_workflow_progress(job.workflow_id)

    async def _update_workflow_progress(self, workflow_id: str) -> None:
        """
        Aggregate progress/status across all jobs in a workflow:

        - progress = mean(job.progress)
        - status:
            RUNNING  if any RUNNING
            FAILED   if any FAILED
            SUCCEEDED if all SUCCEEDED/CANCELLED
            PENDING  otherwise (only PENDING/CANCELLED)
        """
        jobs = await self.store.list_jobs_for_workflow_id(workflow_id)
        wf = await self.store.get_workflow(workflow_id)
        if wf is None:
            return

        if not jobs:
            wf.progress = 0.0
            wf.status = WorkflowStatus.PENDING
            await self.store.update_workflow(wf)
            return

        wf.progress = sum(j.progress for j in jobs) / len(jobs)

        states = {j.state for j in jobs}
        if JobState.RUNNING in states:
            wf.status = WorkflowStatus.RUNNING
        elif JobState.FAILED in states:
            wf.status = WorkflowStatus.FAILED
        elif all(s in {JobState.SUCCEEDED, JobState.CANCELLED} for s in states):
            wf.status = WorkflowStatus.SUCCEEDED
        else:
            wf.status = WorkflowStatus.PENDING

        await self.store.update_workflow(wf)