from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ApprovalClass(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    AUTO_APPROVED = "auto_approved"


class PolicyDecisionStatus(str, Enum):
    APPROVED = "approved"
    PENDING_APPROVAL = "pending_approval"
    REJECTED = "rejected"


class BudgetScopeType(str, Enum):
    GLOBAL = "global"
    WORKSPACE = "workspace"
    AGENT_PROFILE = "agent_profile"
    WORK_ITEM_KIND = "work_item_kind"


class BudgetPolicySnapshot(BaseModel):
    scope_type: BudgetScopeType
    scope_id: str
    daily_cost_limit_usd: float
    per_task_cost_limit_usd: float
    token_limit_per_task: int
    concurrency_limit: int
    enabled: bool = True


class ScopePolicyEvaluation(BaseModel):
    scope_type: BudgetScopeType
    scope_id: str
    daily_cost_limit_usd: float | None = None
    per_task_cost_limit_usd: float | None = None
    token_limit_per_task: int | None = None
    concurrency_limit: int | None = None
    current_daily_spend_usd: float = 0.0
    remaining_budget_usd: float | None = None
    active_task_count: int = 0
    daily_budget_exceeded: bool = False
    per_task_limit_exceeded: bool = False
    token_limit_exceeded: bool = False
    at_concurrency_limit: bool = False


class PolicyEvaluationRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    work_item_id: str = Field(min_length=1)
    task_run_id: UUID | None = None
    workspace_id: str = Field(min_length=1)
    work_item_kind: str = Field(min_length=1)
    requested_by: str = Field(min_length=1)
    requested_from: str = Field(default="human-reviewers", min_length=1)
    risk_level: RiskLevel
    approval_policy: ApprovalClass = Field(
        validation_alias=AliasChoices("approval_policy", "approval_class")
    )
    agent_profile_id: UUID | None = None
    matrix_room_id: str | None = None
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    estimated_token_count: int | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ApprovalRecord(BaseModel):
    id: UUID
    work_item_id: str
    task_run_id: UUID | None = None
    gate_name: str
    requested_from: str
    decision: ApprovalStatus | None = None
    decided_at: datetime | None = None
    decided_by: str | None = None
    evidence_json: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PolicyDecision(BaseModel):
    allowed: bool
    decision: PolicyDecisionStatus
    reason: str
    requires_human_approval: bool = False
    approval_record: ApprovalRecord | None = None
    budget_exceeded: bool = False
    remaining_budget_usd: float | None = None
    at_concurrency_limit: bool = False
    violated_policies: list[str] = Field(default_factory=list)
    scope_evaluations: list[ScopePolicyEvaluation] = Field(default_factory=list)


class ApprovalDecisionRequest(BaseModel):
    approval_id: UUID
    decided_by: str = Field(min_length=1)
    notes: str | None = None
    evidence_json: dict[str, Any] = Field(default_factory=dict)
