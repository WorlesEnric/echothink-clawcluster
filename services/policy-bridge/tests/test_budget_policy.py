from uuid import uuid4

import pytest

from models.policy import BudgetPolicySnapshot, BudgetScopeType, PolicyEvaluationRequest, RiskLevel
from policies.budget import BudgetPolicy


class FakeStorage:
    def __init__(self, *, policies, spend, active_tasks) -> None:
        self.policies = policies
        self.spend = spend
        self.active_tasks = active_tasks

    async def fetch_budget_policy(self, *, scope_type, scope_id):
        return self.policies.get((scope_type, scope_id))

    async def get_daily_spend(self, *, scope_type, scope_id, on_date):
        return self.spend.get((scope_type, scope_id), 0.0)

    async def get_active_task_count(self, *, scope_type, scope_id):
        return self.active_tasks.get((scope_type, scope_id), 0)


def build_request(**overrides) -> PolicyEvaluationRequest:
    payload = {
        "work_item_id": "wi_123",
        "task_run_id": uuid4(),
        "workspace_id": "ws-1",
        "work_item_kind": "code.implement",
        "requested_by": "manager",
        "requested_from": "approvers",
        "risk_level": RiskLevel.MEDIUM,
        "approval_policy": "medium",
        "agent_profile_id": uuid4(),
        "estimated_cost_usd": 3.0,
        "estimated_token_count": 1000,
    }
    payload.update(overrides)
    return PolicyEvaluationRequest.model_validate(payload)


@pytest.mark.anyio
async def test_budget_policy_allows_request_within_limits():
    workspace_policy = BudgetPolicySnapshot(
        scope_type=BudgetScopeType.WORKSPACE,
        scope_id="ws-1",
        daily_cost_limit_usd=10.0,
        per_task_cost_limit_usd=5.0,
        token_limit_per_task=5000,
        concurrency_limit=3,
        enabled=True,
    )
    storage = FakeStorage(
        policies={(BudgetScopeType.WORKSPACE, "ws-1"): workspace_policy},
        spend={(BudgetScopeType.WORKSPACE, "ws-1"): 4.0},
        active_tasks={(BudgetScopeType.WORKSPACE, "ws-1"): 1},
    )
    policy = BudgetPolicy(storage=storage)

    result = await policy.evaluate(build_request())

    assert result.budget_exceeded is False
    assert result.at_concurrency_limit is False
    assert result.remaining_budget_usd == pytest.approx(6.0)
    assert result.violated_policies == []


@pytest.mark.anyio
async def test_budget_policy_flags_daily_limit_and_concurrency():
    workspace_policy = BudgetPolicySnapshot(
        scope_type=BudgetScopeType.WORKSPACE,
        scope_id="ws-1",
        daily_cost_limit_usd=10.0,
        per_task_cost_limit_usd=20.0,
        token_limit_per_task=5000,
        concurrency_limit=2,
        enabled=True,
    )
    storage = FakeStorage(
        policies={(BudgetScopeType.WORKSPACE, "ws-1"): workspace_policy},
        spend={(BudgetScopeType.WORKSPACE, "ws-1"): 9.5},
        active_tasks={(BudgetScopeType.WORKSPACE, "ws-1"): 2},
    )
    policy = BudgetPolicy(storage=storage)

    result = await policy.evaluate(build_request(estimated_cost_usd=1.0))

    assert result.budget_exceeded is True
    assert result.at_concurrency_limit is True
    assert any("daily cost limit exceeded" in violation for violation in result.violated_policies)
    assert any("concurrency limit reached" in violation for violation in result.violated_policies)
