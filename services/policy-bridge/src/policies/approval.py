import logging
from dataclasses import dataclass

from models.policy import ApprovalClass, ApprovalRecord, PolicyEvaluationRequest, RiskLevel
from storage.matrix import MatrixNotifier
from storage.supabase import SupabaseStorage


@dataclass(slots=True)
class ApprovalEvaluation:
    allowed: bool
    requires_human_approval: bool
    reason: str
    approval_record: ApprovalRecord | None = None


class ApprovalPolicy:
    def __init__(self, storage: SupabaseStorage, notifier: MatrixNotifier) -> None:
        self._storage = storage
        self._notifier = notifier
        self._logger = logging.getLogger(__name__)

    async def evaluate(self, request: PolicyEvaluationRequest) -> ApprovalEvaluation:
        if self._is_auto_approved(
            approval_class=request.approval_policy,
            risk_level=request.risk_level,
        ):
            return ApprovalEvaluation(
                allowed=True,
                requires_human_approval=False,
                reason="Approval policy allows automatic progression",
            )

        approval_record = await self._storage.create_approval_request(
            work_item_id=request.work_item_id,
            task_run_id=request.task_run_id,
            gate_name=f"approval:{request.approval_policy.value}",
            requested_from=request.requested_from,
            evidence_json={
                "approval_policy": request.approval_policy.value,
                "risk_level": request.risk_level.value,
                "requested_by": request.requested_by,
                "metadata": request.metadata,
            },
            notes="Human approval required before work can proceed",
        )

        try:
            await self._notifier.post_approval_request(request=request, approval_record=approval_record)
        except Exception:
            self._logger.exception(
                "Created approval record but failed to notify Matrix",
                extra={"work_item_id": request.work_item_id, "approval_id": str(approval_record.id)},
            )

        return ApprovalEvaluation(
            allowed=False,
            requires_human_approval=True,
            reason="Human approval is required for this risk and approval policy combination",
            approval_record=approval_record,
        )

    def _is_auto_approved(self, *, approval_class: ApprovalClass, risk_level: RiskLevel) -> bool:
        if approval_class == ApprovalClass.NONE:
            return True
        if approval_class == ApprovalClass.LOW:
            return risk_level in {RiskLevel.LOW, RiskLevel.MEDIUM}
        if approval_class == ApprovalClass.MEDIUM:
            return risk_level == RiskLevel.LOW
        if approval_class in {ApprovalClass.HIGH, ApprovalClass.CRITICAL}:
            return False
        return False
