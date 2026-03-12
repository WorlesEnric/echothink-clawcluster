from dataclasses import dataclass
from datetime import UTC, datetime

from models.policy import (
    BudgetPolicySnapshot,
    BudgetScopeType,
    PolicyEvaluationRequest,
    ScopePolicyEvaluation,
)
from storage.supabase import SupabaseStorage


@dataclass(slots=True)
class BudgetEvaluation:
    budget_exceeded: bool
    remaining_budget_usd: float | None
    at_concurrency_limit: bool
    violated_policies: list[str]
    scope_evaluations: list[ScopePolicyEvaluation]


class BudgetPolicy:
    def __init__(self, storage: SupabaseStorage) -> None:
        self._storage = storage

    async def evaluate(self, request: PolicyEvaluationRequest) -> BudgetEvaluation:
        scope_evaluations: list[ScopePolicyEvaluation] = []
        violated_policies: list[str] = []
        remaining_budget_usd: float | None = None
        budget_exceeded = False
        at_concurrency_limit = False

        for scope_type, scope_id in self._iter_scopes(request=request):
            policy = await self._storage.fetch_budget_policy(scope_type=scope_type, scope_id=scope_id)
            if policy is None:
                continue

            scope_evaluation = await self._evaluate_scope(policy=policy, request=request)
            scope_evaluations.append(scope_evaluation)

            if scope_evaluation.remaining_budget_usd is not None:
                if remaining_budget_usd is None:
                    remaining_budget_usd = scope_evaluation.remaining_budget_usd
                else:
                    remaining_budget_usd = min(remaining_budget_usd, scope_evaluation.remaining_budget_usd)

            if scope_evaluation.daily_budget_exceeded:
                budget_exceeded = True
                violated_policies.append(f"{scope_type.value}:{scope_id} daily cost limit exceeded")

            if scope_evaluation.per_task_limit_exceeded:
                budget_exceeded = True
                violated_policies.append(f"{scope_type.value}:{scope_id} per-task cost limit exceeded")

            if scope_evaluation.token_limit_exceeded:
                budget_exceeded = True
                violated_policies.append(f"{scope_type.value}:{scope_id} token limit exceeded")

            if scope_evaluation.at_concurrency_limit:
                at_concurrency_limit = True
                violated_policies.append(f"{scope_type.value}:{scope_id} concurrency limit reached")

        return BudgetEvaluation(
            budget_exceeded=budget_exceeded,
            remaining_budget_usd=remaining_budget_usd,
            at_concurrency_limit=at_concurrency_limit,
            violated_policies=violated_policies,
            scope_evaluations=scope_evaluations,
        )

    async def _evaluate_scope(
        self,
        *,
        policy: BudgetPolicySnapshot,
        request: PolicyEvaluationRequest,
    ) -> ScopePolicyEvaluation:
        evaluation_date = datetime.now(tz=UTC).date()
        current_daily_spend = await self._storage.get_daily_spend(
            scope_type=policy.scope_type,
            scope_id=policy.scope_id,
            on_date=evaluation_date,
        )
        active_task_count = await self._storage.get_active_task_count(
            scope_type=policy.scope_type,
            scope_id=policy.scope_id,
        )

        estimated_cost = request.estimated_cost_usd or 0.0
        estimated_tokens = request.estimated_token_count or 0
        remaining_budget = max(policy.daily_cost_limit_usd - current_daily_spend, 0.0)

        return ScopePolicyEvaluation(
            scope_type=policy.scope_type,
            scope_id=policy.scope_id,
            daily_cost_limit_usd=policy.daily_cost_limit_usd,
            per_task_cost_limit_usd=policy.per_task_cost_limit_usd,
            token_limit_per_task=policy.token_limit_per_task,
            concurrency_limit=policy.concurrency_limit,
            current_daily_spend_usd=current_daily_spend,
            remaining_budget_usd=remaining_budget,
            active_task_count=active_task_count,
            daily_budget_exceeded=(current_daily_spend + estimated_cost) > policy.daily_cost_limit_usd,
            per_task_limit_exceeded=estimated_cost > policy.per_task_cost_limit_usd,
            token_limit_exceeded=estimated_tokens > policy.token_limit_per_task,
            at_concurrency_limit=active_task_count >= policy.concurrency_limit,
        )

    def _iter_scopes(self, request: PolicyEvaluationRequest) -> list[tuple[BudgetScopeType, str]]:
        scopes: list[tuple[BudgetScopeType, str]] = [(BudgetScopeType.GLOBAL, "global")]
        scopes.append((BudgetScopeType.WORKSPACE, request.workspace_id))
        scopes.append((BudgetScopeType.WORK_ITEM_KIND, request.work_item_kind))
        if request.agent_profile_id is not None:
            scopes.append((BudgetScopeType.AGENT_PROFILE, str(request.agent_profile_id)))
        return scopes
