from models.policy import PolicyDecision, PolicyDecisionStatus, PolicyEvaluationRequest
from policies.approval import ApprovalPolicy
from policies.budget import BudgetPolicy


class PolicyEvaluator:
    def __init__(self, approval_policy: ApprovalPolicy, budget_policy: BudgetPolicy) -> None:
        self._approval_policy = approval_policy
        self._budget_policy = budget_policy

    async def evaluate(self, request: PolicyEvaluationRequest) -> PolicyDecision:
        budget_evaluation = await self._budget_policy.evaluate(request)
        if budget_evaluation.budget_exceeded or budget_evaluation.at_concurrency_limit:
            return PolicyDecision(
                allowed=False,
                decision=PolicyDecisionStatus.REJECTED,
                reason="Budget policy blocked the work item",
                budget_exceeded=budget_evaluation.budget_exceeded,
                remaining_budget_usd=budget_evaluation.remaining_budget_usd,
                at_concurrency_limit=budget_evaluation.at_concurrency_limit,
                violated_policies=budget_evaluation.violated_policies,
                scope_evaluations=budget_evaluation.scope_evaluations,
            )

        approval_evaluation = await self._approval_policy.evaluate(request)
        if approval_evaluation.requires_human_approval:
            return PolicyDecision(
                allowed=False,
                decision=PolicyDecisionStatus.PENDING_APPROVAL,
                reason=approval_evaluation.reason,
                requires_human_approval=True,
                approval_record=approval_evaluation.approval_record,
                budget_exceeded=budget_evaluation.budget_exceeded,
                remaining_budget_usd=budget_evaluation.remaining_budget_usd,
                at_concurrency_limit=budget_evaluation.at_concurrency_limit,
                violated_policies=budget_evaluation.violated_policies,
                scope_evaluations=budget_evaluation.scope_evaluations,
            )

        return PolicyDecision(
            allowed=True,
            decision=PolicyDecisionStatus.APPROVED,
            reason="Policy checks passed",
            budget_exceeded=budget_evaluation.budget_exceeded,
            remaining_budget_usd=budget_evaluation.remaining_budget_usd,
            at_concurrency_limit=budget_evaluation.at_concurrency_limit,
            violated_policies=budget_evaluation.violated_policies,
            scope_evaluations=budget_evaluation.scope_evaluations,
        )
