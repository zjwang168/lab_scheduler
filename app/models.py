from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class JobType(str, Enum):
    CELL_SEGMENTATION = "cell_segmentation"
    TISSUE_MASK = "tissue_mask"


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
    status: WorkflowStatus = WorkflowStatus.PENDING
    progress: float = 0.0
    created_at: datetime = Field(default_factory=datetime.utcnow)


class JobCreate(BaseModel):
    workflow_id: str
    branch_id: str
    job_type: JobType
    image_path: str
    params: Dict[str, Any] = Field(default_factory=dict)


class Job(BaseModel):
    job_id: str
    workflow_id: str
    user_id: str
    branch_id: str
    job_type: JobType
    image_path: str
    params: Dict[str, Any] = Field(default_factory=dict)

    state: JobState = JobState.PENDING
    progress: float = 0.0

    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    result_path: Optional[str] = None
    error_message: Optional[str] = None