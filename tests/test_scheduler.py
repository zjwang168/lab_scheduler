from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Awaitable, Callable

import pytest

from app.scheduler import Scheduler
from app.config import Settings
from app.models import JobCreate, JobState, JobType


async def wait_until(predicate: Callable[[], Awaitable[bool]], timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Condition not satisfied within timeout")


def make_settings(tmp_path: Path, **overrides) -> Settings:
    base = dict(
        MAX_WORKERS=4,
        MAX_ACTIVE_USERS=3,
        USER_JOB_RATE_LIMIT=100,
        USER_JOB_RATE_INTERVAL_SECONDS=0.1,
        RESULTS_DIR=str(tmp_path / "results"),
        TILE_SIZE=32,
        TILE_OVERLAP=0,
    )
    base.update(overrides)
    return Settings(**base)


def job_payload(workflow_id: str, branch: str, image_path: Path) -> JobCreate:
    return JobCreate(
        workflow_id=workflow_id,
        branch_id=branch,
        job_type=JobType.CELL_SEGMENTATION,
        image_path=str(image_path),
        params={},
    )


@pytest.mark.asyncio
async def test_branch_jobs_run_serially(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    scheduler = Scheduler(settings=settings)
    await scheduler.start()

    fake_img = tmp_path / "branch.svs"
    fake_img.write_text("fake")

    timeline: list[tuple[str, str]] = []

    async def stub_run(job, store):
        timeline.append(("start", job.job_id))
        await asyncio.sleep(0.05)
        timeline.append(("end", job.job_id))
        return str(tmp_path / f"{job.job_id}.json")

    monkeypatch.setattr("app.scheduler.run_worker_job", stub_run)

    user = "branch-user"
    wf = await scheduler.create_workflow(user, "wf")
    job1 = await scheduler.enqueue_job(user, job_payload(wf.workflow_id, "branch-a", fake_img))
    job2 = await scheduler.enqueue_job(user, job_payload(wf.workflow_id, "branch-a", fake_img))

    async def both_done() -> bool:
        j1 = await scheduler.store.get_job(job1.job_id)
        j2 = await scheduler.store.get_job(job2.job_id)
        return j1.state == JobState.SUCCEEDED and j2.state == JobState.SUCCEEDED

    try:
        await wait_until(both_done)
        first = await scheduler.store.get_job(job1.job_id)
        second = await scheduler.store.get_job(job2.job_id)
        assert first.completed_at <= second.started_at
        assert [mark for mark, _ in timeline] == ["start", "end", "start", "end"]
    finally:
        await scheduler.stop()


@pytest.mark.asyncio
async def test_max_active_users_limit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = make_settings(tmp_path, MAX_ACTIVE_USERS=2)
    scheduler = Scheduler(settings=settings)
    await scheduler.start()

    fake_img = tmp_path / "limit.svs"
    fake_img.write_text("fake")

    gate = asyncio.Event()
    concurrent_users: list[int] = []

    async def stub_run(job, store):
        concurrent_users.append(len(scheduler._active_users))
        await gate.wait()
        return str(tmp_path / f"{job.job_id}.json")

    monkeypatch.setattr("app.scheduler.run_worker_job", stub_run)

    jobs = []
    for idx in range(3):
        user = f"tenant-{idx}"
        wf = await scheduler.create_workflow(user, f"wf-{idx}")
        jobs.append(await scheduler.enqueue_job(user, job_payload(wf.workflow_id, "branch", fake_img)))

    async def two_running() -> bool:
        return len(concurrent_users) >= 2

    try:
        await wait_until(two_running)
        pending_third = await scheduler.store.get_job(jobs[2].job_id)
        assert pending_third.state == JobState.PENDING
        assert max(concurrent_users) <= settings.MAX_ACTIVE_USERS

        gate.set()

        async def all_done() -> bool:
            return all((await scheduler.store.get_job(j.job_id)).state == JobState.SUCCEEDED for j in jobs)

        await wait_until(all_done)
    finally:
        await scheduler.stop()


@pytest.mark.asyncio
async def test_cancel_pending_job(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    scheduler = Scheduler(settings=settings)

    fake_img = tmp_path / "cancel.svs"
    fake_img.write_text("fake")

    user = "cancel-user"
    wf = await scheduler.create_workflow(user, "wf")
    job = await scheduler.enqueue_job(user, job_payload(wf.workflow_id, "branch", fake_img))

    cancelled = await scheduler.cancel_job(user, job.job_id)
    assert cancelled.state == JobState.CANCELLED
    assert job.job_id not in scheduler._pending_queue
