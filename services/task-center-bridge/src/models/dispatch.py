from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator


def _strip_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


class DispatchRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    dispatch_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    task_node_id: str | None = None
    title: str | None = None
    objective: str | None = None
    summary: str | None = None
    task_type: str | None = None
    priority: int = Field(default=50, ge=0, le=100)
    risk_level: str | None = "medium"
    success_mode: str | None = "best_effort"
    approval_policy: str | None = "medium"
    execution_family: str | None = Field(
        default=None,
        validation_alias=AliasChoices("execution_family", "preferred_worker_family", "assigned_execution_family"),
    )
    requested_by: str | None = None
    context: dict[str, Any] = Field(default_factory=dict, validation_alias=AliasChoices("context", "context_json"))
    linked_entities: list[dict[str, Any]] = Field(
        default_factory=list,
        validation_alias=AliasChoices("linked_entities", "entity_refs"),
    )
    acceptance_specs: list[dict[str, Any]] = Field(default_factory=list)
    artifacts_prefix: str | None = None
    spec_uri: str | None = None

    @field_validator(
        "dispatch_id",
        "task_id",
        "workspace_id",
        "task_node_id",
        "title",
        "objective",
        "summary",
        "task_type",
        "risk_level",
        "success_mode",
        "approval_policy",
        "execution_family",
        "requested_by",
        "artifacts_prefix",
        "spec_uri",
        mode="before",
    )
    @classmethod
    def _normalize_strings(cls, value: Any) -> Any:
        if value is None or not isinstance(value, str):
            return value
        return value.strip()

    @model_validator(mode="after")
    def _fill_display_fields(self) -> "DispatchRequest":
        if not self.title:
            self.title = self.summary or self.objective or f"Dispatch {self.dispatch_id}"
        if not self.summary:
            self.summary = self.objective or self.title
        if not self.objective:
            self.objective = self.summary or self.title
        if not self.execution_family:
            self.execution_family = "coding"
        if not self.requested_by:
            self.requested_by = "taskcenter"
        return self


class DispatchAcceptedResponse(BaseModel):
    dispatch_id: str
    accepted: bool = True
    work_item_id: str
    status: str
    correlation_ref: str
    processing: dict[str, Any] = Field(default_factory=dict)


class DispatchSnapshot(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    dispatch_id: str
    task_id: str
    task_node_id: str | None = None
    workspace_id: str
    work_item_id: str
    state: str
    dispatch_payload: dict[str, Any] = Field(default_factory=dict)
    bridge_response: dict[str, Any] = Field(default_factory=dict)
    sync_state: dict[str, Any] = Field(default_factory=dict)
    work_item_status: str | None = None
    task_run_id: str | None = None
    task_run_status: str | None = None
    task_run_result_summary: str | None = None
    task_run_error_message: str | None = None
    approval_id: str | None = None
    approval_decision: str | None = None
    approval_notes: str | None = None
    matrix_room_id: str | None = None
    external_refs: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def processing(self) -> dict[str, Any]:
        return dict(self.bridge_response.get("processing") or {})


class DispatchStatusResponse(BaseModel):
    dispatch_id: str
    task_id: str
    task_node_id: str | None = None
    workspace_id: str
    status: str
    correlation_ref: str
    work_item_id: str
    task_run_id: str | None = None
    work_item_status: str | None = None
    task_run_status: str | None = None
    approval_state: str | None = None
    matrix_room_id: str | None = None
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    external_refs: dict[str, Any] = Field(default_factory=dict)
    processing: dict[str, Any] = Field(default_factory=dict)
    sync_state: dict[str, Any] = Field(default_factory=dict)


class OutboxRecord(BaseModel):
    id: str
    dispatch_id: str
    event_type: str
    payload_json: dict[str, Any]
    retry_count: int = 0


class HealthResponse(BaseModel):
    status: str
    service: str = "task-center-bridge"
    dependencies: dict[str, dict[str, Any]] = Field(default_factory=dict)
