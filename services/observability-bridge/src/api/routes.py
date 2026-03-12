import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from auth import require_worker_token
from linkers.graphiti import GraphitiClient
from linkers.langfuse import LangfuseLinker
from linkers.supabase import SupabaseTaskRunStore
from models.events import TaskCompleteEvent, TaskCompleteResult, TaskRunState, TraceLinkRequest, TraceMetrics, TraceSyncRequest, TraceSyncResult


router = APIRouter()
logger = logging.getLogger(__name__)


def get_langfuse_linker(request: Request) -> LangfuseLinker:
    return request.app.state.langfuse_linker


def get_graphiti_client(request: Request) -> GraphitiClient:
    return request.app.state.graphiti_client


def get_task_run_store(request: Request) -> SupabaseTaskRunStore:
    return request.app.state.task_run_store


@router.post(
    "/trace/link",
    response_model=TaskRunState,
    dependencies=[Depends(require_worker_token)],
)
async def link_trace(
    payload: TraceLinkRequest,
    store: SupabaseTaskRunStore = Depends(get_task_run_store),
) -> TaskRunState:
    task_run = await store.update_trace_link(task_run_id=payload.task_run_id, trace_id=payload.trace_id)
    if task_run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task run not found")
    return task_run


@router.post(
    "/trace/sync",
    response_model=TraceSyncResult,
    dependencies=[Depends(require_worker_token)],
)
async def sync_trace(
    payload: TraceSyncRequest,
    linker: LangfuseLinker = Depends(get_langfuse_linker),
    store: SupabaseTaskRunStore = Depends(get_task_run_store),
) -> TraceSyncResult:
    trace_id = payload.trace_id or await store.get_trace_id(task_run_id=payload.task_run_id)
    if not trace_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="trace_id is required or must already be linked to the task run",
        )

    metrics = await linker.fetch_trace_metrics(trace_id=trace_id)
    task_run = await store.update_trace_metrics(task_run_id=payload.task_run_id, metrics=metrics)
    if task_run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task run not found")
    return TraceSyncResult(task_run=task_run, metrics=metrics)


@router.post(
    "/event/complete",
    response_model=TaskCompleteResult,
    dependencies=[Depends(require_worker_token)],
)
async def complete_task(
    payload: TaskCompleteEvent,
    linker: LangfuseLinker = Depends(get_langfuse_linker),
    store: SupabaseTaskRunStore = Depends(get_task_run_store),
    graphiti: GraphitiClient = Depends(get_graphiti_client),
) -> TaskCompleteResult:
    task_run = await store.complete_task_run(payload)
    if task_run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task run not found")

    trace_synced = False
    trace_id = payload.trace_id or task_run.trace_id
    if trace_id:
        try:
            metrics = await linker.fetch_trace_metrics(trace_id=trace_id)
            synced = await store.update_trace_metrics(task_run_id=payload.task_run_id, metrics=metrics)
            if synced is not None:
                task_run = synced
                trace_synced = True
        except Exception:
            logger.exception(
                "Trace sync failed during completion event",
                extra={"task_run_id": str(payload.task_run_id), "trace_id": trace_id},
            )

    graphiti_sync_requested = payload.sync_graphiti and graphiti.enabled
    graphiti_sync_completed = False
    if graphiti_sync_requested:
        try:
            await graphiti.sync_task(payload)
            graphiti_sync_completed = True
        except Exception:
            logger.exception(
                "Graphiti sync failed during completion event",
                extra={"task_run_id": str(payload.task_run_id)},
            )

    return TaskCompleteResult(
        task_run=task_run,
        trace_synced=trace_synced,
        graphiti_sync_requested=graphiti_sync_requested,
        graphiti_sync_completed=graphiti_sync_completed,
    )


@router.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok", "service": "observability-bridge"}
