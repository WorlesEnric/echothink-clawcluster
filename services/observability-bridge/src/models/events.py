from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class TaskRunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TraceMetrics(BaseModel):
    trace_id: str
    cost_usd: float | None = None
    token_count: int | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class TaskRunState(BaseModel):
    task_run_id: UUID
    work_item_id: str | None = None
    status: TaskRunStatus | None = None
    trace_id: str | None = None
    cost_usd: float | None = None
    token_count: int | None = None
    ended_at: datetime | None = None
    result_summary: str | None = None
    error_message: str | None = None


class TraceLinkRequest(BaseModel):
    task_run_id: UUID
    trace_id: str = Field(min_length=1)


class TraceSyncRequest(BaseModel):
    task_run_id: UUID
    trace_id: str | None = None


class TaskCompleteEvent(BaseModel):
    task_run_id: UUID
    work_item_id: str = Field(min_length=1)
    status: TaskRunStatus
    trace_id: str | None = None
    completed_at: datetime | None = None
    result_summary: str | None = None
    error_message: str | None = None
    sync_graphiti: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class TraceSyncResult(BaseModel):
    task_run: TaskRunState
    metrics: TraceMetrics


class TaskCompleteResult(BaseModel):
    task_run: TaskRunState
    trace_synced: bool = False
    graphiti_sync_requested: bool = False
    graphiti_sync_completed: bool = False
