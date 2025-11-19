# app/models.py
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class JobState(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class WorkflowStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class WorkflowCreate(BaseModel):
    name: str


class Workflow(BaseModel):
    workflow_id: str
    user_id: str
    name: str
    created_at: datetime
    status: WorkflowStatus = WorkflowStatus.PENDING
    progress: float = 0.0
    # branch_id -> list[job_id] （本 demo 暂时没用到，但可以保留）
    branches: Dict[str, List[str]] = Field(default_factory=dict)


class JobCreate(BaseModel):
    workflow_id: str
    branch_id: str
    job_type: str
    image_path: str
    params: Dict[str, str] = Field(default_factory=dict)


class Job(BaseModel):
    job_id: str
    user_id: str
    workflow_id: str
    branch_id: str
    job_type: str
    image_path: str
    params: Dict[str, str]

    state: JobState = JobState.PENDING
    progress: float = 0.0

    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None
    result_path: Optional[str] = None