from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def generate_work_item_id() -> str:
    return f"wi_{uuid4().hex}"


def _normalize_line(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


class WorkItemKind(str, Enum):
    code_implement = "code.implement"
    code_review = "code.review"
    workflow_author = "workflow.author"
    plan_breakdown = "plan.breakdown"
    plan_support = "plan.support"
    plan_status = "plan.status"
    knowledge_sync = "knowledge.sync"
    qa_validate = "qa.validate"


class SourceType(str, Enum):
    outline_document = "outline_document"
    gitlab_issue = "gitlab_issue"
    gitlab_mr = "gitlab_mr"
    manual = "manual"


class RiskLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class ApprovalPolicy(str, Enum):
    none = "none"
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class WorkItemLifecycleStatus(str, Enum):
    pending = "pending"
    assigned = "assigned"
    in_progress = "in_progress"
    blocked = "blocked"
    awaiting_approval = "awaiting_approval"
    approved = "approved"
    publishing = "publishing"
    complete = "complete"
    failed = "failed"
    cancelled = "cancelled"


class WorkItemBase(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    workspace_id: str = Field(min_length=1)
    kind: WorkItemKind
    source_type: SourceType = SourceType.manual
    source_ref: str | None = None
    objective: str = Field(min_length=1)
    acceptance_criteria: list[str] = Field(default_factory=list)
    constraints_json: dict[str, Any] = Field(default_factory=dict)
    priority: int = Field(default=50, ge=1, le=100)
    risk_level: RiskLevel = RiskLevel.medium
    approval_policy: ApprovalPolicy = ApprovalPolicy.medium
    requested_by: str = Field(min_length=1)

    @field_validator("workspace_id", "objective", "requested_by", mode="before")
    @classmethod
    def _normalize_required_strings(cls, value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("Expected string field")
        normalized = _normalize_line(value)
        if not normalized:
            raise ValueError("String value cannot be empty")
        return normalized

    @field_validator("source_ref", mode="before")
    @classmethod
    def _normalize_optional_source_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = _normalize_line(value)
        return normalized or None

    @field_validator("acceptance_criteria", mode="before")
    @classmethod
    def _normalize_acceptance_criteria(cls, value: list[str] | None) -> list[str]:
        if value is None:
            return []
        normalized: list[str] = []
        for item in value:
            cleaned = _normalize_line(item)
            if cleaned and cleaned not in normalized:
                normalized.append(cleaned)
        return normalized

    @field_validator("constraints_json", mode="before")
    @classmethod
    def _normalize_constraints_json(cls, value: dict[str, Any] | None) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise TypeError("constraints_json must be an object")
        return {key: item for key, item in value.items() if item is not None}


class WorkItemCreate(WorkItemBase):
    id: str = Field(default_factory=generate_work_item_id, pattern=r"^wi_[0-9a-f]{32}$")

    def render_spec_markdown(self) -> str:
        source_title = self.constraints_json.get("source_title")
        source_content = self.constraints_json.get("source_content")

        lines = [f"# {source_title or self.objective}", "", "## Objective", self.objective, ""]

        if self.acceptance_criteria:
            lines.extend(["## Acceptance Criteria", *[f"- {item}" for item in self.acceptance_criteria], ""])

        lines.extend(
            [
                "## Context",
                f"- Workspace: {self.workspace_id}",
                f"- Kind: {self.kind.value}",
                f"- Source Type: {self.source_type.value}",
                f"- Source Ref: {self.source_ref or 'n/a'}",
                f"- Requested By: {self.requested_by}",
                f"- Priority: {self.priority}",
                f"- Risk Level: {self.risk_level.value}",
                f"- Approval Policy: {self.approval_policy.value}",
                "",
            ]
        )

        if source_content:
            lines.extend(["## Source Content", str(source_content).strip(), ""])

        return "\n".join(lines).strip() + "\n"


class WorkItem(WorkItemCreate):
    status: WorkItemLifecycleStatus = WorkItemLifecycleStatus.pending
    created_at: datetime
    updated_at: datetime


class WorkItemStatus(BaseModel):
    id: str = Field(pattern=r"^wi_[0-9a-f]{32}$")
    status: WorkItemLifecycleStatus
    updated_at: datetime
