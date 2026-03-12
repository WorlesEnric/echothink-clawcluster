import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from auth import require_worker_token
from models.policy import ApprovalDecisionRequest, ApprovalRecord, ApprovalStatus, PolicyDecision, PolicyEvaluationRequest
from policies.evaluator import PolicyEvaluator
from storage.supabase import SupabaseStorage


router = APIRouter()
logger = logging.getLogger(__name__)


def get_storage(request: Request) -> SupabaseStorage:
    return request.app.state.supabase_storage


def get_policy_evaluator(request: Request) -> PolicyEvaluator:
    return request.app.state.policy_evaluator


@router.post(
    "/policy/evaluate",
    response_model=PolicyDecision,
    dependencies=[Depends(require_worker_token)],
)
async def evaluate_policy(
    payload: PolicyEvaluationRequest,
    evaluator: PolicyEvaluator = Depends(get_policy_evaluator),
) -> PolicyDecision:
    logger.info(
        "Evaluating policy request",
        extra={"work_item_id": payload.work_item_id, "task_run_id": str(payload.task_run_id) if payload.task_run_id else None},
    )
    return await evaluator.evaluate(payload)


@router.post(
    "/policy/approve",
    response_model=ApprovalRecord,
    dependencies=[Depends(require_worker_token)],
)
async def approve_policy(
    payload: ApprovalDecisionRequest,
    storage: SupabaseStorage = Depends(get_storage),
) -> ApprovalRecord:
    approval = await storage.record_approval_decision(
        approval_id=payload.approval_id,
        decision=ApprovalStatus.APPROVED,
        decided_by=payload.decided_by,
        notes=payload.notes,
        evidence_json=payload.evidence_json,
    )
    if approval is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval not found")
    return approval


@router.post(
    "/policy/reject",
    response_model=ApprovalRecord,
    dependencies=[Depends(require_worker_token)],
)
async def reject_policy(
    payload: ApprovalDecisionRequest,
    storage: SupabaseStorage = Depends(get_storage),
) -> ApprovalRecord:
    approval = await storage.record_approval_decision(
        approval_id=payload.approval_id,
        decision=ApprovalStatus.REJECTED,
        decided_by=payload.decided_by,
        notes=payload.notes,
        evidence_json=payload.evidence_json,
    )
    if approval is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval not found")
    return approval


@router.get("/policy/pending", response_model=list[ApprovalRecord])
async def list_pending_approvals(
    work_item_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    storage: SupabaseStorage = Depends(get_storage),
) -> list[ApprovalRecord]:
    return await storage.list_pending_approvals(work_item_id=work_item_id, limit=limit)


@router.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok", "service": "policy-bridge"}
