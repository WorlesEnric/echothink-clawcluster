from datetime import UTC, datetime
from uuid import uuid4

import pytest

from models.policy import ApprovalClass, ApprovalRecord, ApprovalStatus, PolicyEvaluationRequest, RiskLevel
from policies.approval import ApprovalPolicy


class FakeStorage:
    def __init__(self) -> None:
        self.created_records: list[ApprovalRecord] = []

    async def create_approval_request(self, **kwargs) -> ApprovalRecord:
        record = ApprovalRecord(
            id=uuid4(),
            work_item_id=kwargs["work_item_id"],
            task_run_id=kwargs.get("task_run_id"),
            gate_name=kwargs["gate_name"],
            requested_from=kwargs["requested_from"],
            decision=ApprovalStatus.PENDING,
            evidence_json=kwargs["evidence_json"],
            notes=kwargs.get("notes"),
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
        )
        self.created_records.append(record)
        return record


class FakeNotifier:
    def __init__(self) -> None:
        self.notifications: list[tuple[str, str]] = []

    async def post_approval_request(self, request, approval_record) -> None:
        self.notifications.append((request.work_item_id, str(approval_record.id)))


def build_request(*, approval_policy: ApprovalClass, risk_level: RiskLevel) -> PolicyEvaluationRequest:
    return PolicyEvaluationRequest(
        work_item_id="wi_123",
        task_run_id=uuid4(),
        workspace_id="ws-1",
        work_item_kind="code.implement",
        requested_by="manager",
        requested_from="approvers",
        approval_policy=approval_policy,
        risk_level=risk_level,
        matrix_room_id="!room:example.com",
        estimated_cost_usd=1.25,
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("approval_policy", "risk_level", "requires_human"),
    [
        (ApprovalClass.NONE, RiskLevel.CRITICAL, False),
        (ApprovalClass.LOW, RiskLevel.LOW, False),
        (ApprovalClass.LOW, RiskLevel.MEDIUM, False),
        (ApprovalClass.LOW, RiskLevel.HIGH, True),
        (ApprovalClass.MEDIUM, RiskLevel.LOW, False),
        (ApprovalClass.MEDIUM, RiskLevel.MEDIUM, True),
        (ApprovalClass.HIGH, RiskLevel.LOW, True),
        (ApprovalClass.CRITICAL, RiskLevel.LOW, True),
    ],
)
async def test_approval_policy_matrix(approval_policy, risk_level, requires_human):
    storage = FakeStorage()
    notifier = FakeNotifier()
    policy = ApprovalPolicy(storage=storage, notifier=notifier)

    result = await policy.evaluate(build_request(approval_policy=approval_policy, risk_level=risk_level))

    assert result.requires_human_approval is requires_human
    assert result.allowed is (not requires_human)
    assert (result.approval_record is not None) is requires_human
    assert len(storage.created_records) == (1 if requires_human else 0)
    assert len(notifier.notifications) == (1 if requires_human else 0)
