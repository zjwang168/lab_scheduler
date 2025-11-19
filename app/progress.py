from __future__ import annotations

from typing import List

from .models import Job, JobState, WorkflowStatus
from .storage import InMemoryStore


async def recompute_workflow_progress(store: InMemoryStore, workflow_id: str) -> None:
    """Recalculate workflow progress/status based on current job states."""
    workflow = await store.get_workflow(workflow_id)
    if workflow is None:
        return

    jobs: List[Job] = await store.list_jobs_for_workflow(workflow_id)
    if not jobs:
        workflow.progress = 0.0
        workflow.status = WorkflowStatus.PENDING
    else:
        workflow.progress = sum(job.progress for job in jobs) / len(jobs)
        if any(job.state == JobState.FAILED for job in jobs):
            workflow.status = WorkflowStatus.FAILED
        elif all(job.state == JobState.SUCCEEDED for job in jobs):
            workflow.status = WorkflowStatus.SUCCEEDED
        elif any(job.state == JobState.RUNNING for job in jobs):
            workflow.status = WorkflowStatus.RUNNING
        else:
            workflow.status = WorkflowStatus.PENDING

    await store.update_workflow(workflow)
