from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from .config import Settings, settings
from .models import Job, JobCreate, JobState, JobType, Workflow, WorkflowCreate, WorkflowStatus
from .storage import InMemoryStore
from .progress import recompute_workflow_progress
from .workers import run_job as run_worker_job
from prometheus_client import Counter, Gauge


job_enqueued_counter = Counter(
    "scheduler_jobs_enqueued_total",
    "Total number of jobs enqueued",
    ["user_id", "job_type"],
)

job_completed_counter = Counter(
    "scheduler_jobs_completed_total",
    "Total number of jobs completed",
    ["user_id", "job_type", "state"],
)

queue_depth_gauge = Gauge(
    "scheduler_pending_jobs",
    "Current number of pending jobs",
)

active_users_gauge = Gauge(
    "scheduler_active_users",
    "Number of users with running jobs",
)


rate_limited_counter = Counter(
    "scheduler_rate_limited_total",
    "Number of times a job dispatch was rate limited",
    ["user_id"],
)


class Scheduler:
    """
    Branch-aware, multi-tenant, in-memory scheduler.

    * Per-branch FIFO: we never run two jobs from the same (user, branch) at once
    * Per-user limit: at most MAX_ACTIVE_USERS users with RUNNING jobs
    * Global limit: at most MAX_WORKERS jobs running at once
    """

    def __init__(self, settings: Settings = settings):
        self.settings = settings
        self.store = InMemoryStore()

        # job_id queue in FIFO order
        self._pending_queue: List[str] = []

        # concurrency bookkeeping
        self._active_users: Set[str] = set()
        self._running_by_branch: Set[Tuple[str, str]] = set()  # (user_id, branch_id)
        self._running_count_by_user: Dict[str, int] = {}

        self._worker_semaphore = asyncio.Semaphore(settings.MAX_WORKERS)
        self._event = asyncio.Event()
        self._stop = False
        self._loop_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

        # per-user rate limiting
        self._user_dispatch_history: Dict[str, List[float]] = {}

    # -------------------- lifecycle --------------------

    async def start(self) -> None:
        if self._loop_task is None:
            self._stop = False
            self._loop_task = asyncio.create_task(self._dispatcher_loop())

    async def stop(self) -> None:
        self._stop = True
        self._event.set()
        if self._loop_task:
            await self._loop_task
            self._loop_task = None

    # ----------------- public API: workflows -----------------

    async def create_workflow(self, user_id: str, name: str) -> Workflow:
        wf = Workflow(
            workflow_id=str(uuid.uuid4()),
            user_id=user_id,
            name=name,
            status=WorkflowStatus.PENDING,
            progress=0.0,
        )
        await self.store.add_workflow(wf)
        return wf

    async def list_workflows_for_user(self, user_id: str) -> List[Workflow]:
        return await self.store.list_workflows_for_user(user_id)

    # ----------------- public API: jobs -----------------

    async def enqueue_job(self, user_id: str, payload: JobCreate) -> Job:
        wf = await self.store.get_workflow(payload.workflow_id)
        if wf is None or wf.user_id != user_id:
            raise ValueError("Workflow not found for this user")

        job = Job(
            job_id=str(uuid.uuid4()),
            workflow_id=payload.workflow_id,
            user_id=user_id,
            branch_id=payload.branch_id,
            job_type=payload.job_type,
            image_path=payload.image_path,
            params=payload.params,
        )
        await self.store.add_job(job)
        await self.store.attach_job_to_workflow(payload.workflow_id, job.job_id)

        async with self._lock:
            self._pending_queue.append(job.job_id)
            queue_depth_gauge.set(len(self._pending_queue))
        self._event.set()
        job_enqueued_counter.labels(user_id=user_id, job_type=job.job_type.value).inc()
        return job

    async def list_jobs_for_workflow(self, user_id: str, workflow_id: str) -> List[Job]:
        wf = await self.store.get_workflow(workflow_id)
        if wf is None or wf.user_id != user_id:
            return []
        return await self.store.list_jobs_for_workflow(workflow_id)

    async def cancel_job(self, user_id: str, job_id: str) -> Job:
        job = await self.store.get_job(job_id)
        if job is None or job.user_id != user_id:
            raise ValueError("Job not found")
        # Only cancellable while pending
        if job.state == JobState.PENDING:
            job.state = JobState.CANCELLED
            await self.store.update_job(job)
            async with self._lock:
                if job_id in self._pending_queue:
                    self._pending_queue.remove(job_id)
        return job

    # ----------------- dispatcher loop -----------------

    async def _dispatcher_loop(self) -> None:
        while not self._stop:
            await self._event.wait()
            self._event.clear()

            progressed = True
            while progressed and not self._stop:
                progressed = False
                async with self._lock:
                    pending_ids = list(self._pending_queue)
                    queue_depth_gauge.set(len(self._pending_queue))

                for job_id in pending_ids:
                    job = await self.store.get_job(job_id)
                    if job is None or job.state != JobState.PENDING:
                        async with self._lock:
                            if job_id in self._pending_queue:
                                self._pending_queue.remove(job_id)
                        continue

                    if not self._can_run(job):
                        continue

                    # dequeue and start
                    async with self._lock:
                        if job_id in self._pending_queue:
                            self._pending_queue.remove(job_id)
                        else:
                            continue

                    asyncio.create_task(self._run_single_job(job.job_id))
                    progressed = True

            # Avoid busy loop
            await asyncio.sleep(0.05)

    def _can_run(self, job: Job) -> bool:
        # User concurrency: if user not active yet and already at limit
        if job.user_id not in self._active_users and len(self._active_users) >= self.settings.MAX_ACTIVE_USERS:
            return False

        # Rate limiting per user
        if not self._within_rate_limit(job.user_id):
            rate_limited_counter.labels(user_id=job.user_id).inc()
            return False

        # Branch FIFO: only one job per (user, branch)
        if (job.user_id, job.branch_id) in self._running_by_branch:
            return False

        # Global workers: check semaphore value
        if self._worker_semaphore.locked() and self._worker_semaphore._value <= 0:  # type: ignore[attr-defined]
            return False

        return True

    def _within_rate_limit(self, user_id: str) -> bool:
        now = datetime.utcnow()
        history = self._user_dispatch_history.get(user_id, [])
        while history and now - datetime.fromtimestamp(history[0]) > timedelta(minutes=1):
            history.pop(0)
        if len(history) >= self.settings.MAX_DISPATCHES_PER_MINUTE:
            return False
        history.append(now.timestamp())
        self._user_dispatch_history[user_id] = history
        return True

    async def _run_single_job(self, job_id: str) -> None:
        await self._worker_semaphore.acquire()
        try:
            job = await self.store.get_job(job_id)
            if job is None or job.state != JobState.PENDING:
                return

            # mark running
            job.state = JobState.RUNNING
            job.started_at = datetime.utcnow()
            await self.store.update_job(job)

            # update concurrency bookkeeping
            self._active_users.add(job.user_id)
            self._running_by_branch.add((job.user_id, job.branch_id))
            self._running_count_by_user[job.user_id] = self._running_count_by_user.get(job.user_id, 0) + 1
            active_users_gauge.set(len(self._active_users))
            self._record_dispatch(job.user_id)

            # also mark workflow as RUNNING
            wf = await self.store.get_workflow(job.workflow_id)
            if wf is not None and wf.status == WorkflowStatus.PENDING:
                wf.status = WorkflowStatus.RUNNING
                await self.store.update_workflow(wf)

            try:
                result_path = await run_worker_job(job, self.store)
                job.result_path = result_path
                job.state = JobState.SUCCEEDED
            except Exception as exc:  # pragma: no cover - error path
                job.state = JobState.FAILED
                job.error_message = str(exc)

            job.completed_at = datetime.utcnow()
            await self.store.update_job(job)
            job_completed_counter.labels(
                user_id=job.user_id,
                job_type=job.job_type.value,
                state=job.state.value,
            ).inc()

            # recompute workflow progress / status
            await recompute_workflow_progress(self.store, job.workflow_id)

        finally:
            # release bookkeeping
            job = await self.store.get_job(job_id)
            if job is not None:
                key = (job.user_id, job.branch_id)
                self._running_by_branch.discard(key)

                cnt = self._running_count_by_user.get(job.user_id, 0)
                if cnt > 1:
                    self._running_count_by_user[job.user_id] = cnt - 1
                elif cnt == 1:
                    del self._running_count_by_user[job.user_id]
                    # if no pending jobs for this user, free slot
                    has_pending_for_user = False
                    async with self._lock:
                        for jid in self._pending_queue:
                            j = await self.store.get_job(jid)
                            if j and j.user_id == job.user_id:
                                has_pending_for_user = True
                                break
                    if not has_pending_for_user:
                        self._active_users.discard(job.user_id)
                        active_users_gauge.set(len(self._active_users))

            self._worker_semaphore.release()
            # Wake dispatcher to schedule next job (possibly new user)
            self._event.set()